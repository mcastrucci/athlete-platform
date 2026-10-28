"""Liveness and empty workers must not wake a suspended database."""

import asyncio
from typing import NoReturn

import httpx
import pytest
from sqlalchemy.engine import Engine

from athlete_platform.api.main import create_app
from athlete_platform.config import Settings
from athlete_platform.worker.main import main


def refuse_connection(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("Bootstrap attempted a database connection")


def test_health_does_not_access_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Engine, "connect", refuse_connection)
    monkeypatch.setenv("ATHLETE_DATABASE_URL", "postgresql+psycopg://user:pass@127.0.0.1:1/app")

    async def request_health() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/health/live")

    response = asyncio.run(request_health())
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_empty_worker_exits_without_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Engine, "connect", refuse_connection)
    assert main() == 0


@pytest.mark.parametrize("token,expected", [(None, 401), ("unverified", 503)])
def test_profile_access_fails_closed_before_database_access(
    token: str | None, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Engine, "connect", refuse_connection)
    app = create_app(settings=Settings(environment="test"))

    async def request_profile() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            return await client.get("/v1/me/profile", headers=headers)

    assert asyncio.run(request_profile()).status_code == expected
