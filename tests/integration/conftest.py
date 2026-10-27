"""Integration tests require a disposable PostgreSQL database, never SQLite."""

import os
from collections.abc import Iterator

import pytest
from pydantic import SecretStr
from sqlalchemy.engine import Engine

from athlete_platform.config import Settings
from athlete_platform.infrastructure.database import build_engine


@pytest.fixture(scope="session")
def database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.fail("Set TEST_DATABASE_URL to a disposable PostgreSQL database")
    return value


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    settings = Settings(
        database_url=SecretStr(database_url),
        database_pool_size=1,
        database_max_overflow=0,
    )
    instance = build_engine(settings)
    try:
        yield instance
    finally:
        instance.dispose()
