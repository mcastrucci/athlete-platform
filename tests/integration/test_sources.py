"""Text sources, duplicate decisions, and interpretation revisions on PostgreSQL."""

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from athlete_platform.api.main import create_app
from athlete_platform.application.profiles import ProfileContent
from athlete_platform.application.sources import (
    ExtractionProvenance,
    InterpretationContent,
    InterpretationCorrection,
    SourceConflict,
    SourceNotFound,
    SourceSubmission,
)
from athlete_platform.config import Settings
from athlete_platform.domain.identity import InvalidIdentity, VerifiedIdentity
from athlete_platform.infrastructure.database import build_session_factory, transaction
from athlete_platform.infrastructure.profiles import PostgresProfiles
from athlete_platform.infrastructure.source_models import InterpretationRevision, Source
from athlete_platform.infrastructure.sources import PostgresExtractionResults, PostgresSources

pytestmark = pytest.mark.integration

TEXT = "Monday: back squat 4x5 @ 100 kg RPE 7\nAMRAP 12: 10 wall balls, 8 toes-to-bar → 6 rounds"
CONTENT: dict[str, Any] = {
    "sessions": [
        {
            "status": "performed",
            "date_text": "Monday",
            "items": [
                {
                    "exercise_text": "back squat",
                    "kind": "strength",
                    "sets": 4,
                    "reps": 5,
                    "load": {"value": 100, "unit": "kg"},
                    "rpe": 7,
                },
                {"exercise_text": "AMRAP 12", "kind": "conditioning", "rounds": 6, "reps": None},
            ],
            "ambiguities": [{"field": "session_date", "note": "Only a weekday was given"}],
        }
    ]
}


@pytest.fixture(autouse=True)
def migrated(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATHLETE_MIGRATION_DATABASE_URL", database_url)
    command.upgrade(Config("alembic.ini"), "head")


def run_client(database_url: str, scenario: Callable[[httpx.AsyncClient], Awaitable[None]]) -> None:
    issuer = f"https://synthetic.invalid/{uuid4()}"

    class SyntheticVerifier:
        def verify(self, token: str) -> VerifiedIdentity:
            if token not in ("alice", "bob"):
                raise InvalidIdentity
            return VerifiedIdentity(issuer, token)

    app = create_app(
        settings=Settings(environment="test", database_url=SecretStr(database_url)),
        test_identity_verifier=SyntheticVerifier(),
    )

    async def run() -> None:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                await scenario(client)

    asyncio.run(run())


def auth(subject: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {subject}"}


async def athlete(client: httpx.AsyncClient, subject: str) -> str:
    response = await client.post("/v1/athletes", json={"timezone": "UTC"}, headers=auth(subject))
    assert response.status_code == 201
    return str(response.json()["athlete_id"])


def provenance() -> ExtractionProvenance:
    return ExtractionProvenance(
        run_id=uuid4(), provider="synthetic", model="fake-extractor", prompt_version="test-1"
    )


def actor(engine: Engine) -> tuple[PostgresSources, UUID]:
    identity = VerifiedIdentity("https://synthetic.invalid", str(uuid4()))
    profile = PostgresProfiles(build_session_factory(engine), identity).create(
        ProfileContent(timezone="UTC")
    )
    return PostgresSources(build_session_factory(engine), identity), profile.athlete_id


def test_submission_preserves_text_and_is_idempotent(database_url: str) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        aid = await athlete(client, "alice")
        path = f"/v1/athletes/{aid}/sources"
        body = {"idempotency_key": "first", "text": TEXT}
        created = await client.post(path, json=body, headers=auth("alice"))
        assert created.status_code == 201
        source = created.json()
        assert source["text"] == TEXT
        assert source["duplicate_status"] == "none"
        assert source["interpretation_version"] is None

        replay = await client.post(path, json=body, headers=auth("alice"))
        assert replay.status_code == 200
        assert replay.json() == source

        changed = await client.post(
            path, json={"idempotency_key": "first", "text": TEXT + "!"}, headers=auth("alice")
        )
        assert changed.status_code == 409

        read = await client.get(f"{path}/{source['id']}", headers=auth("alice"))
        assert read.json() == source

    run_client(database_url, scenario)


def test_submission_validation(database_url: str) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        aid = await athlete(client, "alice")
        path = f"/v1/athletes/{aid}/sources"
        for body in (
            {"idempotency_key": "blank", "text": "   \n"},
            {"idempotency_key": "long", "text": "x" * 20_001},
            {"idempotency_key": " ", "text": TEXT},
            {"idempotency_key": "extra", "text": TEXT, "status": "confirmed"},
        ):
            response = await client.post(path, json=body, headers=auth("alice"))
            assert response.status_code == 422, body
        limit = await client.post(
            path, json={"idempotency_key": "limit", "text": "x" * 20_000}, headers=auth("alice")
        )
        assert limit.status_code == 201

    run_client(database_url, scenario)


def test_identical_text_with_new_key_awaits_explicit_decision(database_url: str) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        aid = await athlete(client, "alice")
        path = f"/v1/athletes/{aid}/sources"
        first = (
            await client.post(
                path, json={"idempotency_key": "a", "text": TEXT}, headers=auth("alice")
            )
        ).json()
        second = (
            await client.post(
                path, json={"idempotency_key": "b", "text": TEXT}, headers=auth("alice")
            )
        ).json()
        assert second["duplicate_of"] == first["id"]
        assert second["duplicate_status"] == "pending"

        different = (
            await client.post(
                path,
                json={"idempotency_key": "c", "text": TEXT.replace("Monday", "Wednesday")},
                headers=auth("alice"),
            )
        ).json()
        assert different["duplicate_status"] == "none"

        decide = f"{path}/{second['id']}/duplicate-decision"
        discarded = await client.post(decide, json={"decision": "discard"}, headers=auth("alice"))
        assert discarded.json()["duplicate_status"] == "discarded"
        repeated = await client.post(decide, json={"decision": "discard"}, headers=auth("alice"))
        assert repeated.status_code == 200
        reversed_decision = await client.post(
            decide, json={"decision": "keep"}, headers=auth("alice")
        )
        assert reversed_decision.status_code == 409
        not_duplicate = await client.post(
            f"{path}/{first['id']}/duplicate-decision",
            json={"decision": "keep"},
            headers=auth("alice"),
        )
        assert not_duplicate.status_code == 409

        third = (
            await client.post(
                path, json={"idempotency_key": "d", "text": TEXT}, headers=auth("alice")
            )
        ).json()
        assert third["duplicate_of"] == first["id"]
        kept = await client.post(
            f"{path}/{third['id']}/duplicate-decision",
            json={"decision": "keep"},
            headers=auth("alice"),
        )
        assert kept.json()["duplicate_status"] == "kept"

    run_client(database_url, scenario)


def test_interpretation_revisions_are_append_only(engine: Engine) -> None:
    sources, aid = actor(engine)
    source, _ = sources.submit(aid, SourceSubmission(idempotency_key="k", text=TEXT))
    with pytest.raises(SourceNotFound):
        sources.interpretation(aid, source.id)

    writer = PostgresExtractionResults(build_session_factory(engine))
    run = provenance()
    extracted = writer.record(source.id, run, InterpretationContent.model_validate(CONTENT))
    assert (extracted.version, extracted.origin, extracted.provenance) == (1, "extraction", run)
    assert writer.record(source.id, run, InterpretationContent()).version == 1

    corrected_content = InterpretationContent.model_validate(CONTENT)
    corrected_content.sessions[0].items[1].reps = 0
    corrected = sources.correct(
        aid, source.id, InterpretationCorrection(expected_version=1, content=corrected_content)
    )
    assert (corrected.version, corrected.origin, corrected.based_on_version) == (2, "correction", 1)
    with pytest.raises(SourceConflict):
        sources.correct(
            aid, source.id, InterpretationCorrection(expected_version=1, content=corrected_content)
        )

    original = sources.interpretation(aid, source.id, 1)
    assert original.content.sessions[0].items[1].reps is None
    latest = sources.interpretation(aid, source.id)
    assert latest.version == 2 and latest.content.sessions[0].items[1].reps == 0
    assert sources.read(aid, source.id).text == TEXT

    rerun = writer.record(source.id, provenance(), InterpretationContent())
    assert (rerun.version, rerun.based_on_version) == (3, None)


def test_extraction_waits_for_duplicate_decision(engine: Engine) -> None:
    sources, aid = actor(engine)
    sources.submit(aid, SourceSubmission(idempotency_key="a", text=TEXT))
    duplicate, _ = sources.submit(aid, SourceSubmission(idempotency_key="b", text=TEXT))
    writer = PostgresExtractionResults(build_session_factory(engine))
    with pytest.raises(SourceConflict):
        writer.record(duplicate.id, provenance(), InterpretationContent())


def test_athletes_cannot_reach_each_other(database_url: str) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        alice = await athlete(client, "alice")
        await athlete(client, "bob")
        path = f"/v1/athletes/{alice}/sources"
        source = (
            await client.post(
                path, json={"idempotency_key": "a", "text": TEXT}, headers=auth("alice")
            )
        ).json()
        requests = [
            client.post(path, json={"idempotency_key": "x", "text": TEXT}, headers=auth("bob")),
            client.get(f"{path}/{source['id']}", headers=auth("bob")),
            client.get(f"{path}/{source['id']}/interpretation", headers=auth("bob")),
            client.put(
                f"{path}/{source['id']}/interpretation",
                json={"expected_version": 1, "content": CONTENT},
                headers=auth("bob"),
            ),
            client.post(
                f"{path}/{source['id']}/duplicate-decision",
                json={"decision": "discard"},
                headers=auth("bob"),
            ),
        ]
        for request in requests:
            assert (await request).status_code == 404
        assert (await client.get(f"{path}/{source['id']}")).status_code == 401

    run_client(database_url, scenario)


def test_correction_contract_keeps_unknown_distinct_from_zero(database_url: str) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        aid = await athlete(client, "alice")
        path = f"/v1/athletes/{aid}/sources"
        source = (
            await client.post(
                path, json={"idempotency_key": "a", "text": TEXT}, headers=auth("alice")
            )
        ).json()
        url = f"{path}/{source['id']}/interpretation"
        missing = await client.put(
            url, json={"expected_version": 1, "content": CONTENT}, headers=auth("alice")
        )
        assert missing.status_code == 409
        invalid = [
            {"sessions": [{"items": [{"exercise_text": "squat", "reps": -1}]}]},
            {"sessions": [{"items": [{"exercise_text": "squat", "rpe": 11}]}]},
            {"sessions": [{"items": [{"exercise_text": "squat", "load": {"value": 1}}]}]},
            {"sessions": [{"status": "confirmed"}]},
            {"sessions": [], "completed": True},
        ]
        for content in invalid:
            response = await client.put(
                url, json={"expected_version": 1, "content": content}, headers=auth("alice")
            )
            assert response.status_code == 422, content

    run_client(database_url, scenario)


def test_concurrent_submissions_resolve_consistently(engine: Engine) -> None:
    sources, aid = actor(engine)
    barrier = Barrier(2)

    def submit(key: str) -> tuple[str, bool, str]:
        barrier.wait()
        view, created = sources.submit(aid, SourceSubmission(idempotency_key=key, text=TEXT))
        return str(view.id), created, view.duplicate_status

    with ThreadPoolExecutor(2) as pool:
        same = list(pool.map(submit, ["same", "same"]))
    assert same[0][0] == same[1][0]
    assert sorted(created for _, created, _ in same) == [False, True]

    barrier = Barrier(2)
    with ThreadPoolExecutor(2) as pool:
        different = list(pool.map(submit, ["one", "two"]))
    assert sorted(status for _, _, status in different) == ["pending", "pending"]
    with transaction(build_session_factory(engine)) as session:
        statuses = session.scalars(
            select(Source.duplicate_status).where(Source.athlete_id == aid)
        ).all()
    assert sorted(statuses) == ["none", "pending", "pending"]


def test_database_rejects_cross_athlete_duplicates_and_unbased_corrections(engine: Engine) -> None:
    alice, alice_id = actor(engine)
    bob, bob_id = actor(engine)
    alice_source, _ = alice.submit(alice_id, SourceSubmission(idempotency_key="a", text=TEXT))
    bob_source, _ = bob.submit(bob_id, SourceSubmission(idempotency_key="b", text=TEXT))
    factory = build_session_factory(engine)
    with pytest.raises(IntegrityError), transaction(factory) as session:
        row = session.get(Source, bob_source.id)
        assert row is not None
        row.duplicate_of = alice_source.id
        row.duplicate_status = "pending"
    with pytest.raises(IntegrityError), transaction(factory) as session:
        session.add(
            InterpretationRevision(
                source_id=alice_source.id,
                version=1,
                athlete_id=alice_id,
                origin="correction",
                based_on_version=None,
                run_id=None,
                provenance=None,
                schema_version=1,
                content={},
            )
        )
