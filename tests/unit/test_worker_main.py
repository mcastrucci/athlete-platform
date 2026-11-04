"""Worker wiring for the Anthropic route; no network, database connection, or real key."""

from typing import Any, NoReturn

import pytest
from pydantic import SecretStr
from sqlalchemy.engine import Engine

from athlete_platform.application.jobs import WorkerQueue
from athlete_platform.config import Settings
from athlete_platform.infrastructure.anthropic_extractor import AnthropicClient
from athlete_platform.infrastructure.jobs import PostgresJobs
from athlete_platform.worker.main import MAX_JOBS, MAX_SECONDS, main
from athlete_platform.worker.runner import Handler

DSN = "postgresql+psycopg://user:pass@127.0.0.1:1/app"
KEY = SecretStr("sk-ant-synthetic-secret-value")


class ForbiddenMessages:
    def create(self, **kwargs: Any) -> NoReturn:
        raise AssertionError("The worker wiring called the provider")


class ForbiddenClient:
    messages = ForbiddenMessages()


class Recorder:
    def __init__(self) -> None:
        self.keys: list[SecretStr] = []
        self.runs: list[tuple[WorkerQueue, Handler, dict[str, Any]]] = []

    def client_factory(self, key: SecretStr) -> AnthropicClient:
        self.keys.append(key)
        return ForbiddenClient()

    def runner(self, queue: WorkerQueue, handler: Handler, **limits: Any) -> int:
        self.runs.append((queue, handler, limits))
        return 0


@pytest.fixture(autouse=True)
def no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("Wiring attempted a database connection")

    monkeypatch.setattr(Engine, "connect", refuse)


def test_configured_worker_builds_the_handler_without_network_or_database() -> None:
    recorder = Recorder()
    settings = Settings(
        extraction_provider="anthropic",
        anthropic_api_key=KEY,
        database_url=SecretStr(DSN),
        extraction_daily_attempts_per_athlete=7,
    )
    assert main(settings, client_factory=recorder.client_factory, runner=recorder.runner) == 0
    assert recorder.keys == [KEY]
    ((queue, handler, limits),) = recorder.runs
    assert limits == {"max_jobs": MAX_JOBS, "max_seconds": MAX_SECONDS}
    assert isinstance(queue, PostgresJobs) and queue.limits.daily_attempts_per_athlete == 7
    assert callable(handler)


def test_default_daily_attempts_are_twenty() -> None:
    recorder = Recorder()
    settings = Settings(
        extraction_provider="anthropic", anthropic_api_key=KEY, database_url=SecretStr(DSN)
    )
    main(settings, client_factory=recorder.client_factory, runner=recorder.runner)
    queue = recorder.runs[0][0]
    assert isinstance(queue, PostgresJobs) and queue.limits.daily_attempts_per_athlete == 20


def test_unconfigured_worker_does_not_build_anything() -> None:
    recorder = Recorder()
    settings = Settings(database_url=SecretStr(DSN))
    assert main(settings, client_factory=recorder.client_factory, runner=recorder.runner) == 0
    assert recorder.keys == [] and recorder.runs == []


def test_provider_without_database_fails_closed() -> None:
    recorder = Recorder()
    settings = Settings(extraction_provider="anthropic", anthropic_api_key=KEY)
    assert main(settings, client_factory=recorder.client_factory, runner=recorder.runner) == 1
    assert recorder.keys == [] and recorder.runs == []


def test_unpriced_model_fails_closed() -> None:
    recorder = Recorder()
    settings = Settings(
        extraction_provider="anthropic",
        anthropic_api_key=KEY,
        database_url=SecretStr(DSN),
        extraction_model="unpriced-model",
    )
    assert main(settings, client_factory=recorder.client_factory, runner=recorder.runner) == 1
    assert recorder.runs == []
