"""Configuration fails early without exposing connection credentials."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from athlete_platform.config import Settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "ATHLETE_DATABASE_URL",
        "ATHLETE_MIGRATION_DATABASE_URL",
        "ATHLETE_DATABASE_POOL_SIZE",
        "ATHLETE_ENVIRONMENT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_environment_loads_separate_runtime_and_direct_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = "postgresql+psycopg://user:secret@pool.example.test/app"
    direct = "postgresql+psycopg://user:secret@direct.example.test/app"
    monkeypatch.setenv("ATHLETE_DATABASE_URL", runtime)
    monkeypatch.setenv("ATHLETE_MIGRATION_DATABASE_URL", direct)
    settings = Settings()
    assert settings.database_dsn() == runtime
    assert settings.database_dsn(migration=True) == direct
    assert "secret" not in repr(settings)


def test_migration_connection_falls_back_to_runtime() -> None:
    dsn = "postgresql+psycopg://user:password@localhost/test"
    settings = Settings(database_url=SecretStr(dsn))
    assert settings.database_dsn(migration=True) == dsn


def test_database_access_requires_explicit_configuration() -> None:
    with pytest.raises(ValueError, match="Configure ATHLETE_DATABASE_URL"):
        Settings().database_dsn()


@pytest.mark.parametrize("dsn", ["sqlite:///test.db", "postgresql+psycopg://", "not-a-url-secret"])
def test_invalid_database_configuration_is_redacted(dsn: str) -> None:
    with pytest.raises(ValidationError) as error:
        Settings(database_url=SecretStr(dsn))
    assert dsn not in str(error.value)


def test_pool_size_must_be_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings(database_pool_size=0)
