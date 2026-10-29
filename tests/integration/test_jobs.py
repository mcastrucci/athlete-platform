"""Exercise real queue concurrency, fencing, idempotency, and quota transactions."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import JsonValue
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import QueuePool

from athlete_platform.application.jobs import (
    FailureCode,
    JobConflict,
    JobLease,
    JobNotFound,
    JobState,
    QueueFull,
    QueueLimits,
)
from athlete_platform.application.profiles import ProfileContent
from athlete_platform.domain.identity import VerifiedIdentity
from athlete_platform.infrastructure.database import build_session_factory, transaction
from athlete_platform.infrastructure.job_models import DailyJobAttempts, ExtractionJob, JobAttempt
from athlete_platform.infrastructure.jobs import PostgresJobs
from athlete_platform.infrastructure.models import Athlete
from athlete_platform.infrastructure.profiles import PostgresProfiles
from athlete_platform.worker.runner import TransientJobError, run_worker

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def isolated_queue(database_url: str, engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATHLETE_MIGRATION_DATABASE_URL", database_url)
    command.upgrade(Config("alembic.ini"), "head")
    # This database has already passed the disposable-test guard; never run on shared data.
    with transaction(build_session_factory(engine)) as session:
        session.execute(delete(ExtractionJob))
        session.execute(delete(DailyJobAttempts))


def actor(engine: Engine) -> tuple[VerifiedIdentity, UUID]:
    identity = VerifiedIdentity("https://synthetic.invalid", str(uuid4()))
    profile = PostgresProfiles(build_session_factory(engine), identity).create(
        ProfileContent(timezone="UTC")
    )
    return identity, profile.athlete_id


def expire(engine: Engine, job_id: UUID) -> None:
    with transaction(build_session_factory(engine)) as session:
        session.execute(
            update(ExtractionJob)
            .where(ExtractionJob.id == job_id)
            .values(lease_expires_at=func.clock_timestamp() - text("interval '1 second'"))
        )


def make_due(engine: Engine, job_id: UUID) -> None:
    with transaction(build_session_factory(engine)) as session:
        session.execute(
            update(ExtractionJob)
            .where(ExtractionJob.id == job_id)
            .values(available_at=func.clock_timestamp() - text("interval '1 second'"))
        )


def test_idempotency_and_ownership_are_enforced(engine: Engine) -> None:
    queue = PostgresJobs(build_session_factory(engine))
    a, aid = actor(engine)
    b, bid = actor(engine)
    job = queue.enqueue(a, aid, "same-key", {"source": "synthetic"})
    assert queue.enqueue(a, aid, "same-key", {"source": "synthetic"}).id == job.id
    assert queue.enqueue(b, bid, "same-key", {"source": "synthetic"}).id != job.id
    with pytest.raises(JobConflict):
        queue.enqueue(a, aid, "same-key", {"source": "changed"})
    with pytest.raises(JobNotFound):
        queue.enqueue(b, aid, "forged", {})
    for operation in (
        lambda: queue.get(b, job.id),
        lambda: queue.history(b, job.id),
        lambda: queue.retry(b, job.id, 0),
    ):
        with pytest.raises(JobNotFound):
            operation()
    with transaction(build_session_factory(engine)) as session:
        owner = session.scalars(select(Athlete.owner_user_id).where(Athlete.id == bid)).one()
    with pytest.raises(IntegrityError), transaction(build_session_factory(engine)) as session:
        session.add(
            ExtractionJob(
                id=uuid4(),
                athlete_id=aid,
                owner_user_id=owner,
                idempotency_key="forged-owner",
                input_hash="synthetic",
                payload={},
                state="queued",
                attempts=0,
                attempt_limit=1,
                available_at=datetime.now(UTC),
            )
        )


def enqueue_pair(
    queue: PostgresJobs, identity: VerifiedIdentity, athlete_id: UUID, sources: list[str]
) -> list[str]:
    barrier = Barrier(2)

    def enqueue(source: str) -> str:
        barrier.wait()
        try:
            return str(queue.enqueue(identity, athlete_id, "race", {"source": source}).id)
        except JobConflict:
            return "conflict"

    with ThreadPoolExecutor(2) as pool:
        return list(pool.map(enqueue, sources))


@pytest.mark.parametrize("isolation", ["READ COMMITTED", "REPEATABLE READ"])
def test_concurrent_enqueues_resolve_to_the_stored_job(engine: Engine, isolation: str) -> None:
    # REPEATABLE READ snapshots miss the winner's row, so the loser hits the unique constraint.
    queue = PostgresJobs(build_session_factory(engine.execution_options(isolation_level=isolation)))
    for _ in range(5):
        identity, aid = actor(engine)
        first, second = enqueue_pair(queue, identity, aid, ["same", "same"])
        assert first == second != "conflict"

        identity, aid = actor(engine)
        outcomes = enqueue_pair(queue, identity, aid, ["one", "other"])
        assert outcomes.count("conflict") == 1


def test_concurrent_claims_and_skip_locked(engine: Engine) -> None:
    factory = build_session_factory(engine)
    queue = PostgresJobs(factory)
    identity, aid = actor(engine)
    first = queue.enqueue(identity, aid, "one", {})
    second = queue.enqueue(identity, aid, "two", {})
    with transaction(factory) as session:
        session.scalar(select(ExtractionJob).where(ExtractionJob.id == first.id).with_for_update())
        with ThreadPoolExecutor(max_workers=1) as pool:
            lease = pool.submit(queue.claim).result(timeout=10)
        assert lease is not None and lease.job.id == second.id
    barrier = Barrier(2)

    def claim() -> JobLease | None:
        barrier.wait(timeout=10)
        return queue.claim()

    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(pool.map(lambda _: claim(), range(2)))
    assert sum(lease is not None for lease in leases) == 1
    assert next(lease for lease in leases if lease is not None).job.id == first.id


def test_expired_lease_is_fenced_and_recovered(engine: Engine) -> None:
    queue = PostgresJobs(build_session_factory(engine))
    identity, aid = actor(engine)
    job = queue.enqueue(identity, aid, "one", {})
    old = queue.claim()
    assert old is not None and queue.heartbeat(old)
    expire(engine, job.id)
    assert not queue.heartbeat(old)
    assert not queue.complete(old, {"late": True})
    assert not queue.fail(old, FailureCode.HANDLER_ERROR, retryable=False)
    new = queue.claim()
    assert new is not None and new.token != old.token and new.job.attempts == 2
    assert not queue.complete(old, {"late": True})
    assert not queue.fail(old, FailureCode.HANDLER_ERROR, retryable=False)
    assert queue.complete(new, {"parsed": True})
    assert queue.complete(new, {"parsed": True})
    assert not queue.complete(new, {"parsed": False})
    assert queue.get(identity, job.id).result == {"parsed": True}
    history = queue.history(identity, job.id)
    assert [row["outcome"] for row in history] == ["expired", "succeeded"]
    assert all(row["finished_at"] is not None for row in history)
    with transaction(build_session_factory(engine)) as session:
        assert session.scalar(select(func.count()).select_from(JobAttempt)) == 2


def test_retries_backoff_and_exhaustion_preserve_history(engine: Engine) -> None:
    queue = PostgresJobs(build_session_factory(engine), QueueLimits(max_attempts=2))
    identity, aid = actor(engine)
    job = queue.enqueue(identity, aid, "retry", {})
    first = queue.claim()
    assert first is not None
    assert queue.fail(first, FailureCode.TRANSIENT, retryable=True)
    deferred = queue.get(identity, job.id)
    assert deferred.state == JobState.DEFERRED
    assert deferred.available_at > datetime.now(UTC)
    assert queue.claim() is None
    make_due(engine, job.id)
    second = queue.claim()
    assert second is not None and second.job.attempts == 2
    expire(engine, job.id)
    assert queue.claim() is None
    failed = queue.get(identity, job.id)
    assert failed.state == JobState.FAILED and failed.error_code == FailureCode.ATTEMPT_LIMIT
    with pytest.raises(JobConflict):
        queue.retry(identity, job.id, 1)
    assert queue.retry(identity, job.id, 2).attempt_limit == 4
    assert queue.retry(identity, job.id, 2).state == JobState.QUEUED
    third = queue.claim()
    assert third is not None and third.job.attempts == 3
    assert queue.fail(third, FailureCode.UNCONFIGURED, retryable=False)
    assert queue.get(identity, job.id).state == JobState.FAILED
    assert [row["outcome"] for row in queue.history(identity, job.id)] == [
        "failed",
        "expired",
        "failed",
    ]


def test_concurrent_submission_quota_has_one_winner(engine: Engine) -> None:
    queue = PostgresJobs(build_session_factory(engine), QueueLimits(active_jobs_per_athlete=1))
    identity, aid = actor(engine)
    barrier = Barrier(2)

    def submit(key: str) -> UUID | None:
        barrier.wait(timeout=10)
        try:
            return queue.enqueue(identity, aid, key, {}).id
        except QueueFull:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ["one", "two"]))
    assert sum(value is not None for value in results) == 1
    assert results.count(None) == 1


def test_daily_quota_is_atomic_and_deferred_work_resumes(engine: Engine) -> None:
    factory = build_session_factory(engine)
    queue = PostgresJobs(factory, QueueLimits(daily_attempts_per_athlete=1))
    identity, aid = actor(engine)
    jobs = [queue.enqueue(identity, aid, key, {}) for key in ("one", "two")]
    barrier = Barrier(2)

    def claim() -> JobLease | None:
        barrier.wait(timeout=10)
        return queue.claim()

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim(), range(2)))
    assert sum(value is not None for value in claims) == 1
    states = [queue.get(identity, job.id) for job in jobs]
    deferred = next(job for job in states if job.state == JobState.DEFERRED)
    assert deferred.attempts == 0 and deferred.available_at.hour == 0
    assert queue.claim() is None
    # Simulate an authorized quota reset without waiting for midnight.
    with transaction(factory) as session:
        session.execute(
            update(DailyJobAttempts).where(DailyJobAttempts.athlete_id == aid).values(count=0)
        )
    make_due(engine, deferred.id)
    resumed = queue.claim()
    assert resumed is not None and resumed.job.id == deferred.id


def test_worker_does_not_hold_connections_and_exits_at_job_budget(engine: Engine) -> None:
    queue = PostgresJobs(build_session_factory(engine))
    identity, aid = actor(engine)
    for key in ("one", "two"):
        queue.enqueue(identity, aid, key, {})

    def handler(lease: JobLease, renew: Callable[[], bool]) -> dict[str, JsonValue]:
        assert cast(QueuePool, engine.pool).checkedout() == 0
        assert renew()
        assert cast(QueuePool, engine.pool).checkedout() == 0
        return {"validated": True}

    assert run_worker(queue, handler, max_jobs=1) == 1
    assert run_worker(queue, handler) == 1
    assert run_worker(queue, handler) == 0


@pytest.mark.parametrize(
    "mode,expected", [("transient", "deferred"), ("invalid", "failed"), ("error", "failed")]
)
def test_worker_persists_sanitized_failures(engine: Engine, mode: str, expected: str) -> None:
    queue = PostgresJobs(build_session_factory(engine))
    identity, aid = actor(engine)
    job = queue.enqueue(identity, aid, "one", {})

    def handler(lease: JobLease, renew: Callable[[], bool]) -> dict[str, JsonValue]:
        if mode == "transient":
            raise TransientJobError("private text must not be stored")
        if mode == "error":
            raise RuntimeError("private text must not be stored")
        return {"invalid": float("nan")}

    assert run_worker(queue, handler) == 1
    result = queue.get(identity, job.id)
    assert result.state.value == expected and result.error_code is not None
    assert "private" not in result.model_dump_json()


def test_worker_honors_runtime_budget_between_jobs(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = PostgresJobs(build_session_factory(engine))
    identity, aid = actor(engine)
    for key in ("one", "two"):
        queue.enqueue(identity, aid, key, {})
    ticks = iter([0.0, 0.0, 61.0])
    monkeypatch.setattr("athlete_platform.worker.runner.monotonic", lambda: next(ticks))
    assert run_worker(queue, lambda lease, renew: {}, max_seconds=60) == 1


def test_manual_retry_respects_active_capacity_and_deleted_work_is_not_republished(
    engine: Engine,
) -> None:
    factory = build_session_factory(engine)
    queue = PostgresJobs(factory, QueueLimits(active_jobs_per_athlete=1))
    identity, aid = actor(engine)
    failed = queue.enqueue(identity, aid, "failed", {})
    old = queue.claim()
    assert old is not None and queue.fail(old, FailureCode.INVALID_OUTPUT, retryable=False)
    active = queue.enqueue(identity, aid, "active", {})
    with pytest.raises(QueueFull):
        queue.retry(identity, failed.id, 1)
    assert queue.get(identity, failed.id).state == JobState.FAILED
    lease = queue.claim()
    assert lease is not None and lease.job.id == active.id
    with transaction(factory) as session:
        session.execute(delete(ExtractionJob).where(ExtractionJob.id == active.id))
    assert not queue.complete(lease, {})
    assert not queue.heartbeat(lease)
    assert not queue.fail(lease, FailureCode.TRANSIENT, retryable=True)
