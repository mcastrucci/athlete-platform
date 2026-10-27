"""Liveness and empty workers must not wake a suspended database."""

from typing import NoReturn

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from athlete_platform.api.main import create_app
from athlete_platform.worker.main import main


def refuse_connection(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("Bootstrap attempted a database connection")


def test_health_does_not_access_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Engine, "connect", refuse_connection)
    monkeypatch.setenv("ATHLETE_DATABASE_URL", "invalid-unused-configuration")
    with TestClient(create_app()) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_empty_worker_exits_without_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Engine, "connect", refuse_connection)
    assert main() == 0
