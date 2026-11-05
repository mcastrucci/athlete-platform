"""Pull-budget evaluations on PostgreSQL: manifests, idempotency, races, staleness, ownership.

Calendar (all invented): the evaluated week is 2030-W10, Monday 2030-03-04 to Sunday 2030-03-10.
The span that can influence it starts six days earlier, on Tuesday 2030-02-26.
"""

import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from athlete_platform.application.evaluation import EvaluationNotFound, EvaluationRequest
from athlete_platform.application.profiles import ProfileContent, ProfileUpdate, PullBudget
from athlete_platform.application.sessions import ConfirmationRequest
from athlete_platform.application.sources import (
    ExtractionProvenance,
    InterpretationContent,
    SourceSubmission,
)
from athlete_platform.config import Settings
from athlete_platform.domain.identity import VerifiedIdentity
from athlete_platform.infrastructure.database import build_engine, build_session_factory
from athlete_platform.infrastructure.database import transaction as db_transaction
from athlete_platform.infrastructure.evaluation_models import EvaluationRow
from athlete_platform.infrastructure.evaluations import PostgresEvaluations
from athlete_platform.infrastructure.profiles import PostgresProfiles
from athlete_platform.infrastructure.sessions import PostgresSessions
from athlete_platform.infrastructure.sources import PostgresExtractionResults, PostgresSources
from tests.integration.test_sessions import athlete, auth, interpreted_source, run_client

pytestmark = pytest.mark.integration

WEEK = "2030-W10"
BUDGETS: list[dict[str, Any]] = [
    {"id": "strict-weekly", "weekly_caps": {"strict_pull": 30, "rope": 10}},
]


@pytest.fixture(autouse=True)
def migrated(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATHLETE_MIGRATION_DATABASE_URL", database_url)
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture
def wide_engine(database_url: str) -> Iterator[Engine]:
    """A pool large enough for every concurrent caller to hold a connection."""
    settings = Settings(
        database_url=SecretStr(database_url), database_pool_size=12, database_max_overflow=0
    )
    instance = build_engine(settings)
    try:
        yield instance
    finally:
        instance.dispose()


def pull(exercise_id: str | None, sets: int | None, reps: int | None) -> dict[str, Any]:
    item: dict[str, Any] = {"exercise_text": exercise_id or "unlabelled movement"}
    if exercise_id is not None:
        item["exercise_id"] = exercise_id
    if sets is not None:
        item["sets"] = sets
    if reps is not None:
        item["reps"] = reps
    return item


def session(day: str, *items: dict[str, Any], status: str = "performed") -> dict[str, Any]:
    return {"status": status, "session_date": day, "items": list(items)}


async def confirmed(
    client: httpx.AsyncClient, engine: Engine, aid: str, *sessions: dict[str, Any]
) -> str:
    source_id = await interpreted_source(
        client, engine, aid, "alice", content={"sessions": list(sessions)}
    )
    response = await client.post(
        f"/v1/athletes/{aid}/sources/{source_id}/confirmation",
        json={"expected_version": 1},
        headers=auth("alice"),
    )
    assert response.status_code == 201
    return source_id


async def set_budgets(
    client: httpx.AsyncClient, aid: str, version: int, budgets: list[dict[str, Any]] | None
) -> int:
    content: dict[str, Any] = {"timezone": "UTC"}
    if budgets is not None:
        content["pull_budgets"] = budgets
    response = await client.put(
        f"/v1/athletes/{aid}/profile",
        json={"expected_version": version, "content": content},
        headers=auth("alice"),
    )
    assert response.status_code == 200, response.text
    return int(response.json()["version"])


async def evaluate(
    client: httpx.AsyncClient, aid: str, week: str = WEEK, subject: str = "alice"
) -> httpx.Response:
    return await client.post(
        f"/v1/athletes/{aid}/evaluations", json={"week": week}, headers=auth(subject)
    )


async def read(
    client: httpx.AsyncClient, aid: str, evaluation_id: str, subject: str = "alice"
) -> httpx.Response:
    return await client.get(
        f"/v1/athletes/{aid}/evaluations/{evaluation_id}", headers=auth(subject)
    )


def evaluation_count(engine: Engine, athlete_id: str) -> int:
    with db_transaction(build_session_factory(engine)) as db:
        return (
            db.scalar(
                select(func.count())
                .select_from(EvaluationRow)
                .where(EvaluationRow.athlete_id == UUID(athlete_id))
            )
            or 0
        )


def test_weekly_and_rolling_findings_with_manifest_and_versions(
    database_url: str, engine: Engine
) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        aid = await athlete(client, "alice")
        profile_version = await set_budgets(client, aid, 1, BUDGETS)
        before = await confirmed(client, engine, aid, session("2030-02-28", pull("pull_up", 3, 8)))
        inside = await confirmed(
            client,
            engine,
            aid,
            session(
                "2030-03-05",
                pull("pull_up", 2, 5),
                pull("rope_climb", 2, 2),
                pull("back_squat", 5, 5),
            ),
        )

        response = await evaluate(client, aid)
        assert response.status_code == 201
        body = response.json()
        assert body["week"] == WEEK
        assert body["athlete_id"] == aid
        assert body["stale"] is False
        assert body["assumptions"] == []
        assert re.fullmatch(r"[0-9a-f]{64}", body["manifest_hash"])
        manifest = body["manifest"]
        assert manifest["week"] == WEEK
        assert manifest["profile_version"] == profile_version == 2
        assert manifest["catalog_version"] == "catalog-v1"
        assert manifest["engine_version"] == "pull-budget-v1"
        assert manifest["confirmations"] == sorted(
            [
                {"source_id": before, "interpretation_version": 1},
                {"source_id": inside, "interpretation_version": 1},
            ],
            key=lambda c: (c["source_id"], c["interpretation_version"]),
        )

        findings = {(f["category"], f["kind"]): f for f in body["findings"]}
        assert set(findings) == {
            ("strict_pull", "weekly"),
            ("rope", "weekly"),
            ("strict_pull", "rolling"),
        }
        weekly = findings["strict_pull", "weekly"]
        assert (weekly["constraint_id"], weekly["value"], weekly["cap"], weekly["status"]) == (
            "strict-weekly",
            10,
            30,
            "ok",
        )
        assert (weekly["window_start"], weekly["window_end"]) == ("2030-03-04", "2030-03-10")
        assert [(c["item_position"], c["pulls"]) for c in weekly["contributions"]] == [(0, 10)]
        assert weekly["catalog_version"] == "catalog-v1"
        assert weekly["engine_version"] == "pull-budget-v1"
        rolling = findings["strict_pull", "rolling"]
        assert (rolling["value"], rolling["status"]) == (34, "fail")
        assert (rolling["window_start"], rolling["window_end"]) == ("2030-02-27", "2030-03-05")
        assert [(c["session_date"], c["pulls"]) for c in rolling["contributions"]] == [
            ("2030-02-28", 24),
            ("2030-03-05", 10),
        ]
        assert (findings["rope", "weekly"]["value"], findings["rope", "weekly"]["status"]) == (
            4,
            "ok",
        )

        stored = await read(client, aid, body["id"])
        assert stored.status_code == 200
        assert stored.json() == body
        listed = await client.get(
            f"/v1/athletes/{aid}/evaluations", params={"week": WEEK}, headers=auth("alice")
        )
        assert listed.status_code == 200
        assert listed.json() == {"evaluations": [body], "has_more": False}

    run_client(database_url, scenario)


def test_repeating_an_evaluation_returns_the_stored_one(database_url: str, engine: Engine) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        aid = await athlete(client, "alice")
        await set_budgets(client, aid, 1, BUDGETS)
        await confirmed(client, engine, aid, session("2030-03-05", pull("pull_up", 2, 5)))
        first = await evaluate(client, aid)
        second = await evaluate(client, aid)
        assert (first.status_code, second.status_code) == (201, 200)
        assert second.json() == first.json()
        assert evaluation_count(engine, aid) == 1
        other_week = await evaluate(client, aid, "2030-W11")
        assert other_week.status_code == 201
        assert other_week.json()["id"] != first.json()["id"]
        assert evaluation_count(engine, aid) == 2

    run_client(database_url, scenario)


def test_a_new_confirmation_in_the_span_makes_the_old_result_stale(
    database_url: str, engine: Engine
) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        aid = await athlete(client, "alice")
        await set_budgets(client, aid, 1, BUDGETS)
        await confirmed(client, engine, aid, session("2030-03-05", pull("pull_up", 2, 5)))
        old = (await evaluate(client, aid)).json()
        await confirmed(client, engine, aid, session("2030-02-27", pull("pull_up", 5, 5)))

        stale = await read(client, aid, old["id"])
        assert stale.json()["stale"] is True
        assert {k: v for k, v in stale.json().items() if k != "stale"} == {
            k: v for k, v in old.items() if k != "stale"
        }  # a stale result stays fully readable and unchanged

        fresh_response = await evaluate(client, aid)
        assert fresh_response.status_code == 201
        fresh = fresh_response.json()
        assert fresh["id"] != old["id"]
        assert len(fresh["manifest"]["confirmations"]) == 2
        findings = {(f["category"], f["kind"]): f for f in fresh["findings"]}
        assert findings["strict_pull", "weekly"]["value"] == 10
        assert findings["strict_pull", "rolling"]["value"] == 35

        listed = await client.get(
            f"/v1/athletes/{aid}/evaluations", params={"week": WEEK}, headers=auth("alice")
        )
        evaluations = listed.json()["evaluations"]
        assert [(e["id"], e["stale"]) for e in evaluations] == [
            (fresh["id"], False),
            (old["id"], True),
        ]
        one = await client.get(
            f"/v1/athletes/{aid}/evaluations",
            params={"week": WEEK, "limit": 1},
            headers=auth("alice"),
        )
        assert [e["id"] for e in one.json()["evaluations"]] == [fresh["id"]]
        assert one.json()["has_more"] is True

    run_client(database_url, scenario)


def test_a_profile_change_makes_the_result_stale(database_url: str, engine: Engine) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        aid = await athlete(client, "alice")
        version = await set_budgets(client, aid, 1, BUDGETS)
        old = (await evaluate(client, aid)).json()
        assert (await read(client, aid, old["id"])).json()["stale"] is False
        await set_budgets(
            client, aid, version, [{"id": "strict-weekly", "weekly_caps": {"rope": 1}}]
        )
        assert (await read(client, aid, old["id"])).json()["stale"] is True
        fresh = (await evaluate(client, aid)).json()
        assert fresh["manifest"]["profile_version"] == 3
        assert [(f["category"], f["cap"]) for f in fresh["findings"]] == [("rope", 1)]

    run_client(database_url, scenario)


def test_confirmations_outside_the_span_do_not_make_the_result_stale(
    database_url: str, engine: Engine
) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        aid = await athlete(client, "alice")
        await set_budgets(client, aid, 1, BUDGETS)
        await confirmed(client, engine, aid, session("2030-03-05", pull("pull_up", 2, 5)))
        old = (await evaluate(client, aid)).json()
        await confirmed(client, engine, aid, session("2030-02-25", pull("pull_up", 9, 9)))
        await confirmed(client, engine, aid, session("2030-03-11", pull("pull_up", 9, 9)))
        assert (await read(client, aid, old["id"])).json()["stale"] is False
        again = await evaluate(client, aid)
        assert again.status_code == 200
        assert again.json()["id"] == old["id"]

    run_client(database_url, scenario)


def test_a_source_that_is_not_confirmed_never_affects_results(
    database_url: str, engine: Engine
) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        aid = await athlete(client, "alice")
        await set_budgets(client, aid, 1, BUDGETS)
        await confirmed(client, engine, aid, session("2030-03-05", pull("pull_up", 2, 5)))
        before = (await evaluate(client, aid)).json()
        unconfirmed = await interpreted_source(
            client,
            engine,
            aid,
            "alice",
            content={"sessions": [session("2030-03-06", pull("pull_up", 20, 20))]},
        )
        assert unconfirmed
        assert (await read(client, aid, before["id"])).json()["stale"] is False
        again = await evaluate(client, aid)
        assert again.status_code == 200
        assert again.json()["findings"] == before["findings"]
        assert unconfirmed not in [
            c["source_id"] for c in again.json()["manifest"]["confirmations"]
        ]

    run_client(database_url, scenario)


def test_unclassified_items_make_the_result_manual_and_are_listed_as_assumptions(
    database_url: str, engine: Engine
) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        aid = await athlete(client, "alice")
        await set_budgets(client, aid, 1, BUDGETS)
        await confirmed(
            client,
            engine,
            aid,
            session(
                "2030-03-05", pull("pull_up", 1, 5), pull(None, 3, 5), pull("mystery_move", 1, 1)
            ),
        )
        body = (await evaluate(client, aid)).json()
        weekly = [f for f in body["findings"] if f["kind"] == "weekly"]
        assert {f["status"] for f in weekly} == {"manual"}
        assert all(f["unknown_dates"] == ["2030-03-05"] for f in weekly)
        assert [(a["code"], a["item_position"]) for a in body["assumptions"]] == [
            ("unclassified_item", 1),
            ("unknown_exercise_id", 2),
        ]
        assert all(a["session_id"] for a in body["assumptions"])

    run_client(database_url, scenario)


def test_no_budgets_still_stores_an_evaluation_with_an_assumption(
    database_url: str, engine: Engine
) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        aid = await athlete(client, "alice")
        await confirmed(client, engine, aid, session("2030-03-05", pull("pull_up", 2, 5)))
        response = await evaluate(client, aid)
        assert response.status_code == 201
        body = response.json()
        assert body["findings"] == []
        assert [a["code"] for a in body["assumptions"]] == ["no_budgets_configured"]
        assert len(body["manifest"]["confirmations"]) == 1
        assert (await evaluate(client, aid)).status_code == 200

    run_client(database_url, scenario)


def test_profiles_stored_without_pull_budgets_still_load(database_url: str, engine: Engine) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        aid = await athlete(client, "alice")
        with db_transaction(build_session_factory(engine)) as db:
            db.execute(
                text(
                    "UPDATE profile_revisions SET content = content - 'pull_budgets' "
                    "WHERE athlete_id = :athlete"
                ),
                {"athlete": aid},
            )
            stored = db.scalar(
                text("SELECT content FROM profile_revisions WHERE athlete_id = :athlete"),
                {"athlete": aid},
            )
        assert stored is not None and "pull_budgets" not in stored
        profile = await client.get(f"/v1/athletes/{aid}/profile", headers=auth("alice"))
        assert profile.status_code == 200
        assert profile.json()["content"]["pull_budgets"] is None
        response = await evaluate(client, aid)
        assert response.status_code == 201
        assert [a["code"] for a in response.json()["assumptions"]] == ["no_budgets_configured"]

    run_client(database_url, scenario)


def test_profile_rejects_invalid_pull_budgets(database_url: str) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        aid = await athlete(client, "alice")
        for bad in (
            [{"id": "a", "weekly_caps": {"rope": -1}}],
            [{"id": "a", "weekly_caps": {}}],
            [{"id": "a", "weekly_caps": {"rope": 1}}, {"id": "a", "weekly_caps": {"rope": 2}}],
            [{"id": "", "weekly_caps": {"rope": 1}}],
        ):
            response = await client.put(
                f"/v1/athletes/{aid}/profile",
                json={"expected_version": 1, "content": {"timezone": "UTC", "pull_budgets": bad}},
                headers=auth("alice"),
            )
            assert response.status_code == 422, bad

    run_client(database_url, scenario)


def test_athletes_cannot_reach_each_others_evaluations(database_url: str, engine: Engine) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        alice = await athlete(client, "alice")
        bob = await athlete(client, "bob")
        await set_budgets(client, alice, 1, BUDGETS)
        mine = (await evaluate(client, alice)).json()

        assert (await evaluate(client, alice, subject="bob")).status_code == 404
        assert (await read(client, alice, mine["id"], subject="bob")).status_code == 404
        listing = await client.get(
            f"/v1/athletes/{alice}/evaluations", params={"week": WEEK}, headers=auth("bob")
        )
        assert listing.status_code == 404
        # Bob's own athlete id never resolves Alice's evaluation.
        assert (await read(client, bob, mine["id"], subject="bob")).status_code == 404
        assert (await read(client, alice, str(uuid4()))).status_code == 404
        assert (await evaluate(client, str(uuid4()))).status_code == 404
        own = await client.get(
            f"/v1/athletes/{bob}/evaluations", params={"week": WEEK}, headers=auth("bob")
        )
        assert own.json() == {"evaluations": [], "has_more": False}
        assert evaluation_count(engine, bob) == 0

        for request in (
            client.post(f"/v1/athletes/{alice}/evaluations", json={"week": WEEK}),
            client.get(f"/v1/athletes/{alice}/evaluations", params={"week": WEEK}),
            client.get(f"/v1/athletes/{alice}/evaluations/{mine['id']}"),
        ):
            assert (await request).status_code == 401

    run_client(database_url, scenario)


def test_invalid_requests_return_422(database_url: str) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        aid = await athlete(client, "alice")
        base = f"/v1/athletes/{aid}/evaluations"
        for body in (
            {"week": "2030-W9"},
            {"week": "2031-W53"},
            {"week": "2030-03-04"},
            {"week": ""},
            {"week": 10},
            {},
            {"week": WEEK, "athlete_id": aid},
        ):
            response = await client.post(base, json=body, headers=auth("alice"))
            assert response.status_code == 422, body
        bad_queries: list[dict[str, str | int]] = [
            {},
            {"week": "nope"},
            {"week": "2030-W00"},
            {"week": WEEK, "limit": 0},
        ]
        for params in bad_queries:
            response = await client.get(base, params=params, headers=auth("alice"))
            assert response.status_code == 422, params
        malformed = await client.get(f"{base}/not-a-uuid", headers=auth("alice"))
        assert malformed.status_code == 422
        stored = await client.get(base, params={"week": WEEK}, headers=auth("alice"))
        assert stored.json() == {"evaluations": [], "has_more": False}

    run_client(database_url, scenario)


# ------------------------------------------------------------------ concurrency


def seed(engine: Engine) -> tuple[PostgresEvaluations, UUID, PostgresProfiles, PostgresSessions]:
    """A synthetic athlete with budgets and one confirmed session inside the span."""
    factory = build_session_factory(engine)
    identity = VerifiedIdentity("https://synthetic.invalid", str(uuid4()))
    profiles = PostgresProfiles(factory, identity)
    athlete_id = profiles.create(ProfileContent(timezone="UTC")).athlete_id
    profiles.update(
        athlete_id,
        ProfileUpdate(
            expected_version=1,
            content=ProfileContent(
                timezone="UTC",
                pull_budgets=[PullBudget(id="b", weekly_caps={"strict_pull": 30})],
            ),
        ),
    )
    sessions = PostgresSessions(factory, identity)
    add_confirmation(factory, identity, sessions, athlete_id, "2030-03-05")
    return PostgresEvaluations(factory, identity), athlete_id, profiles, sessions


def add_confirmation(
    factory: Any,
    identity: VerifiedIdentity,
    sessions: PostgresSessions,
    athlete_id: UUID,
    day: str,
) -> None:
    source, _ = PostgresSources(factory, identity).submit(
        athlete_id, SourceSubmission(idempotency_key=str(uuid4()), text=f"synthetic {uuid4()}")
    )
    PostgresExtractionResults(factory).record(
        source.id,
        ExtractionProvenance(
            run_id=uuid4(), provider="synthetic", model="fake-extractor", prompt_version="test-1"
        ),
        InterpretationContent.model_validate({"sessions": [session(day, pull("pull_up", 2, 5))]}),
    )
    sessions.confirm(athlete_id, source.id, ConfirmationRequest(expected_version=1))


@pytest.mark.parametrize("round_number", [1, 2, 3])
def test_concurrent_identical_requests_store_one_evaluation(
    wide_engine: Engine, round_number: int
) -> None:
    evaluations, athlete_id, _, _ = seed(wide_engine)
    callers = 8
    barrier = Barrier(callers)

    def call() -> tuple[bool, UUID]:
        barrier.wait()
        view, created = evaluations.evaluate(athlete_id, EvaluationRequest(week=WEEK))
        return created, view.id

    with ThreadPoolExecutor(callers) as pool:
        futures = [pool.submit(call) for _ in range(callers)]
        results = [future.result(30) for future in futures]
    assert sum(created for created, _ in results) == 1
    assert len({evaluation_id for _, evaluation_id in results}) == 1
    assert evaluation_count(wide_engine, str(athlete_id)) == 1


def test_the_insert_conflict_path_returns_the_winner_when_the_precheck_misses(
    wide_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    evaluations, athlete_id, _, _ = seed(wide_engine)
    winner, created = evaluations.evaluate(athlete_id, EvaluationRequest(week=WEEK))
    assert created is True
    # Simulate losing the race: the pre-check sees nothing, the unique constraint decides.
    original = PostgresEvaluations._by_hash
    calls: list[int] = []

    def blind_first(*args: Any) -> Any:
        calls.append(1)
        return None if len(calls) == 1 else original(*args)

    monkeypatch.setattr(PostgresEvaluations, "_by_hash", staticmethod(blind_first))
    loser, created = evaluations.evaluate(athlete_id, EvaluationRequest(week=WEEK))
    assert created is False
    assert loser.id == winner.id
    assert len(calls) == 2
    assert evaluation_count(wide_engine, str(athlete_id)) == 1


def test_evaluations_race_with_a_new_confirmation_without_mixing_inputs(
    wide_engine: Engine,
) -> None:
    evaluations, athlete_id, profiles, sessions = seed(wide_engine)
    factory = build_session_factory(wide_engine)
    identity = evaluations.identity
    barrier = Barrier(5)

    def confirm() -> None:
        barrier.wait()
        add_confirmation(factory, identity, sessions, athlete_id, "2030-03-06")

    def evaluate_now() -> UUID:
        barrier.wait()
        return evaluations.evaluate(athlete_id, EvaluationRequest(week=WEEK))[0].id

    with ThreadPoolExecutor(5) as pool:
        confirming = pool.submit(confirm)
        evaluating = [pool.submit(evaluate_now) for _ in range(4)]
        confirming.result(30)
        ids = {future.result(30) for future in evaluating}
    assert 1 <= len(ids) <= 2
    # Every stored result describes exactly the sessions it was computed from.
    with db_transaction(factory) as db:
        for row in db.scalars(select(EvaluationRow).where(EvaluationRow.athlete_id == athlete_id)):
            sources = len(row.manifest["confirmations"])
            weekly = next(f for f in row.findings if f["kind"] == "weekly")
            assert weekly["value"] == 10 * sources
    final, _ = evaluations.evaluate(athlete_id, EvaluationRequest(week=WEEK))
    assert final.stale is False
    assert final.findings[0].value == 20
    assert all(evaluations.read(athlete_id, i).stale == (i != final.id) for i in ids)
    assert profiles.read(athlete_id).version == 2


def test_a_foreign_identity_gets_not_found_from_the_repository(wide_engine: Engine) -> None:
    evaluations, athlete_id, _, _ = seed(wide_engine)
    view, _ = evaluations.evaluate(athlete_id, EvaluationRequest(week=WEEK))
    stranger = PostgresEvaluations(
        build_session_factory(wide_engine), VerifiedIdentity("https://synthetic.invalid", "x")
    )
    with pytest.raises(EvaluationNotFound):
        stranger.read(athlete_id, view.id)
    with pytest.raises(EvaluationNotFound):
        stranger.list_week(athlete_id, WEEK)
    with pytest.raises(EvaluationNotFound):
        stranger.evaluate(athlete_id, EvaluationRequest(week=WEEK))


# ------------------------------------------------------------------ database rules


def test_database_rejects_malformed_or_duplicate_evaluation_rows(wide_engine: Engine) -> None:
    evaluations, athlete_id, _, _ = seed(wide_engine)
    view, _ = evaluations.evaluate(athlete_id, EvaluationRequest(week=WEEK))
    factory = build_session_factory(wide_engine)

    def insert(**overrides: Any) -> None:
        values: dict[str, Any] = {
            "id": uuid4(),
            "athlete_id": athlete_id,
            "week": WEEK,
            "manifest": {},
            "manifest_hash": "f" * 64,
            "findings": [],
            "assumptions": [],
        }
        values.update(overrides)
        with db_transaction(factory) as db:
            db.add(EvaluationRow(**values))

    insert(week="2030-W11")  # a valid row is accepted
    bad_rows: list[dict[str, Any]] = [
        {"manifest_hash": view.manifest_hash},  # duplicate (athlete, week, hash)
        {"week": "2030-10"},
        {"manifest_hash": "ABC"},
        {"findings": {}},
        {"assumptions": {}},
        {"manifest": []},
        {"athlete_id": uuid4()},  # unknown athlete
    ]
    for overrides in bad_rows:
        with pytest.raises(IntegrityError):
            insert(**overrides)
