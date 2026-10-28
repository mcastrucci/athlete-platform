"""Liveness and empty workers must not wake a suspended database."""

import asyncio
from typing import NoReturn

import httpx
import pytest
from sqlalchemy.engine import Engine

from athlete_platform.api.main import create_app
from athlete_platform.worker.main import main


def refuse_connection(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("Bootstrap attempted a database connection")


def test_health_does_not_access_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Engine, "connect", refuse_connection)
    monkeypatch.setenv("ATHLETE_DATABASE_URL", "invalid-unused-configuration")

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
