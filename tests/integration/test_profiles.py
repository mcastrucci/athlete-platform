"""Two-athlete isolation, revision integrity, and concurrent updates on PostgreSQL."""

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import event, func, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from athlete_platform.api.main import create_app
from athlete_platform.application.profiles import ProfileConflict, ProfileContent, ProfileUpdate
from athlete_platform.config import Settings
from athlete_platform.domain.identity import InvalidIdentity, VerifiedIdentity
from athlete_platform.infrastructure.database import build_session_factory, transaction
from athlete_platform.infrastructure.models import Athlete, ProfileRevision
from athlete_platform.infrastructure.profiles import PostgresProfiles

pytestmark = pytest.mark.integration


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


def test_two_athletes_keep_independent_context_and_history(database_url: str) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        alice_content = {
            "timezone": "America/Argentina/Buenos_Aires",
            "equipment": ["barbell"],
            "availability": [{"weekday": 0, "minutes": 60}],
            "commitments": [
                {"description": "Coach session", "flexibility": "fixed", "weekdays": [0]}
            ],
            "baselines": [
                {
                    "metric": "squat",
                    "value": 100,
                    "unit": "kg",
                    "observed_on": "2026-01-01",
                    "source": "self-report",
                }
            ],
        }
        bob_content = {
            "timezone": "Europe/London",
            "equipment": ["rings"],
            "availability": [{"weekday": 2, "minutes": 30}],
            "commitments": [{"description": "Open training", "flexibility": "flexible"}],
        }
        alice = await client.post("/v1/athletes", headers=auth("alice"), json=alice_content)
        bob = await client.post("/v1/athletes", headers=auth("bob"), json=bob_content)
        assert alice.status_code == bob.status_code == 201
        aid, bid = alice.json()["athlete_id"], bob.json()["athlete_id"]
        assert aid != bid
        assert alice.json()["content"]["skills"] is None
        assert bob.json()["content"]["baselines"] is None
        assert alice.json()["content"]["baselines"][0]["source"] == "self-report"
        assert alice.json()["created_at"].endswith("Z")
        assert bob.json()["content"]["commitments"][0]["flexibility"] == "flexible"

        path = f"/v1/athletes/{aid}/profile"
        assert (await client.get(path, headers=auth("bob"))).status_code == 404
        assert (await client.get(path + "?version=1", headers=auth("bob"))).status_code == 404
        assert (
            await client.put(
                path, headers=auth("bob"), json={"expected_version": 1, "content": bob_content}
            )
        ).status_code == 404
        assert (
            await client.post("/v1/athletes", headers=auth("alice"), json=alice_content)
        ).status_code == 409

        updated = {**alice_content, "experience": "Updated synthetic context"}
        response = await client.put(
            path, headers=auth("alice"), json={"expected_version": 1, "content": updated}
        )
        assert response.status_code == 200
        assert response.json()["version"] == 2
        assert (
            await client.put(
                path, headers=auth("alice"), json={"expected_version": 1, "content": alice_content}
            )
        ).status_code == 409
        historical = await client.get(path + "?version=1", headers=auth("alice"))
        assert historical.json()["content"]["experience"] is None
        assert (await client.get(path, headers=auth("alice"))).json()["version"] == 2
        assert (await client.get(f"/v1/athletes/{bid}/profile", headers=auth("bob"))).json()[
            "content"
        ]["equipment"] == ["rings"]

    run_client(database_url, scenario)


def test_unauthenticated_and_forged_input_are_rejected(database_url: str) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        content = {"timezone": "UTC"}
        assert (await client.post("/v1/athletes", json=content)).status_code == 401
        assert (
            await client.post("/v1/athletes", headers=auth("forged"), json=content)
        ).status_code == 401
        assert (
            await client.post(
                "/v1/athletes", headers=auth("alice"), json={**content, "athlete_id": str(uuid4())}
            )
        ).status_code == 422
        assert (
            await client.post(
                "/v1/athletes", headers=auth("alice"), json={"timezone": "Invalid/Timezone"}
            )
        ).status_code == 422

    run_client(database_url, scenario)


def test_database_rejects_a_revision_linked_to_another_owner(engine: Engine) -> None:
    factory = build_session_factory(engine)
    issuer = f"https://synthetic.invalid/{uuid4()}"
    a = PostgresProfiles(factory, VerifiedIdentity(issuer, "a")).create(
        ProfileContent(timezone="UTC")
    )
    b = PostgresProfiles(factory, VerifiedIdentity(issuer, "b")).create(
        ProfileContent(timezone="UTC")
    )
    with transaction(factory) as session:
        owner = session.scalars(
            select(Athlete.owner_user_id).where(Athlete.id == a.athlete_id)
        ).one()
    with pytest.raises(IntegrityError), transaction(factory) as session:
        session.add(
            ProfileRevision(
                athlete_id=b.athlete_id,
                owner_user_id=owner,
                version=999,
                schema_version=1,
                content={},
            )
        )
    with transaction(factory) as session:
        assert session.get(ProfileRevision, (b.athlete_id, 999)) is None


def test_concurrent_updates_have_one_winner_and_no_partial_revision(engine: Engine) -> None:
    factory = build_session_factory(engine)
    repo = PostgresProfiles(factory, VerifiedIdentity("https://synthetic.invalid", str(uuid4())))
    original = repo.create(ProfileContent(timezone="UTC"))
    barrier = Barrier(2)

    def synchronize(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if statement.startswith("UPDATE athletes"):
            barrier.wait(timeout=10)

    def change(value: str) -> int | None:
        try:
            return repo.update(
                original.athlete_id,
                ProfileUpdate(
                    expected_version=1, content=ProfileContent(timezone="UTC", experience=value)
                ),
            ).version
        except ProfileConflict:
            return None

    event.listen(engine, "before_cursor_execute", synchronize)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(change, ["first", "second"]))
    finally:
        event.remove(engine, "before_cursor_execute", synchronize)
    assert sorted(result for result in results if result is not None) == [2]
    assert results.count(None) == 1
    assert repo.read(original.athlete_id).version == 2
    assert repo.read(original.athlete_id, 1).content.experience is None
    with transaction(factory) as session:
        count = session.scalar(
            select(func.count())
            .select_from(ProfileRevision)
            .where(ProfileRevision.athlete_id == original.athlete_id)
        )
        assert count == 2
