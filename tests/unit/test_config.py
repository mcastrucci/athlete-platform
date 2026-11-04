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
        "ATHLETE_EXTRACTION_PROVIDER",
        "ATHLETE_ANTHROPIC_API_KEY",
        "ATHLETE_EXTRACTION_MODEL",
        "ATHLETE_MODEL_BUDGET_USD_MONTHLY",
        "ATHLETE_EXTRACTION_DAILY_ATTEMPTS_PER_ATHLETE",
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


def test_anthropic_provider_requires_a_key_and_never_exposes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError) as error:
        Settings(extraction_provider="anthropic")
    assert "ATHLETE_ANTHROPIC_API_KEY" in str(error.value)

    secret = "sk-ant-synthetic-secret-value"
    settings = Settings(extraction_provider="anthropic", anthropic_api_key=SecretStr(secret))
    assert secret not in repr(settings) and secret not in str(settings)
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == secret
    with pytest.raises(ValidationError) as invalid:
        Settings(
            extraction_provider="anthropic",
            anthropic_api_key=SecretStr(secret),
            extraction_daily_attempts_per_athlete=0,
        )
    assert secret not in str(invalid.value)

    monkeypatch.setenv("ATHLETE_EXTRACTION_PROVIDER", "anthropic")
    monkeypatch.setenv("ATHLETE_ANTHROPIC_API_KEY", secret)
    loaded = Settings()
    assert loaded.extraction_provider == "anthropic"
    assert secret not in repr(loaded)


def test_extraction_defaults_and_bounds() -> None:
    settings = Settings()
    assert settings.extraction_provider is None and settings.anthropic_api_key is None
    assert settings.extraction_model == "claude-haiku-5-5"
    assert settings.model_budget_usd_monthly == 25
    assert settings.extraction_daily_attempts_per_athlete == 20
    for bad in (0, -1, 100_000):
        with pytest.raises(ValidationError):
            Settings(model_budget_usd_monthly=bad)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Settings(extraction_provider="other")  # type: ignore[arg-type]
