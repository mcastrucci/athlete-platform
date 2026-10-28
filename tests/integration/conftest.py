"""Integration tests require a disposable PostgreSQL database, never SQLite."""

import os
from collections.abc import Iterator

import pytest
from pydantic import SecretStr
from sqlalchemy.engine import Engine

from athlete_platform.config import Settings
from athlete_platform.infrastructure.database import build_engine
from tests.database_safety import validate_test_database


@pytest.fixture(scope="session")
def database_url() -> str:
    runtime = Settings().database_url
    try:
        return validate_test_database(
            os.environ.get("TEST_DATABASE_URL"), runtime.get_secret_value() if runtime else None
        )
    except ValueError as error:
        pytest.fail(str(error), pytrace=False)


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    settings = Settings(
        database_url=SecretStr(database_url),
        database_pool_size=2,
        database_max_overflow=0,
    )
    instance = build_engine(settings)
    try:
        yield instance
    finally:
        instance.dispose()
