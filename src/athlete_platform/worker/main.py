"""Worker entry point: drains a bounded amount of extraction work, then exits."""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import SecretStr, ValidationError
from sqlalchemy.engine import Engine

from athlete_platform.application.budget import UnpricedModel, price_for
from athlete_platform.application.jobs import QueueLimits, WorkerQueue
from athlete_platform.config import Settings
from athlete_platform.infrastructure.anthropic_extractor import (
    AnthropicClient,
    AnthropicExtractor,
    build_anthropic_client,
)
from athlete_platform.infrastructure.budget import PostgresBudget
from athlete_platform.infrastructure.database import build_engine, build_session_factory
from athlete_platform.infrastructure.jobs import PostgresJobs
from athlete_platform.worker.extraction import extraction_handler
from athlete_platform.worker.runner import Handler, run_worker

logger = logging.getLogger(__name__)

# One execution is bounded. The runner is sequential, so at most one provider call is in
# flight, within the limit of two concurrent calls.
MAX_JOBS = 10
MAX_SECONDS = 60.0

ClientFactory = Callable[[SecretStr], AnthropicClient]
Runner = Callable[..., int]


@dataclass(frozen=True)
class Worker:
    queue: WorkerQueue
    handler: Handler
    engine: Engine


def build_worker(
    settings: Settings, client_factory: ClientFactory = build_anthropic_client
) -> Worker:
    """Wire the queue, budget gate, Anthropic extractor, and handler. Makes no network call.

    Requires `extraction_provider == "anthropic"`, an API key, and a database URL. The engine
    is lazy; the caller disposes it.
    """
    if settings.extraction_provider != "anthropic" or settings.anthropic_api_key is None:
        raise ValueError("The anthropic provider is not configured")
    price_for(settings.extraction_model)  # Refuse a model whose spend cannot be bounded.
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    limits = QueueLimits(daily_attempts_per_athlete=settings.extraction_daily_attempts_per_athlete)
    extractor = AnthropicExtractor(
        client_factory(settings.anthropic_api_key), settings.extraction_model
    )
    budget = PostgresBudget(factory, settings.model_budget_usd_monthly)
    return Worker(
        queue=PostgresJobs(factory, limits),
        handler=extraction_handler(factory, extractor, budget),
        engine=engine,
    )


def main(
    settings: Settings | None = None,
    *,
    client_factory: ClientFactory = build_anthropic_client,
    runner: Runner = run_worker,
) -> int:
    logging.basicConfig(level=logging.INFO)
    try:
        settings = settings or Settings()
    except ValidationError:
        # Details are omitted on purpose: they could echo configuration values.
        logger.error("Invalid configuration; exiting without polling.")
        return 1
    if settings.extraction_provider is None:
        logger.info("No job handlers are configured; exiting without polling.")
        return 0
    if settings.database_url is None:
        logger.error("The extraction provider needs ATHLETE_DATABASE_URL; exiting without polling.")
        return 1
    try:
        worker = build_worker(settings, client_factory)
    except UnpricedModel:
        logger.error("The extraction model has no configured price; exiting without polling.")
        return 1
    try:
        processed = runner(worker.queue, worker.handler, max_jobs=MAX_JOBS, max_seconds=MAX_SECONDS)
        logger.info("Processed %d extraction job(s).", processed)
    finally:
        worker.engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
