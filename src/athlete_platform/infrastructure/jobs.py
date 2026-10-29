"""PostgreSQL queue; transactions end before a worker calls any handler."""

import hashlib
import json
import random
from datetime import UTC, datetime, time, timedelta
from uuid import UUID, uuid4

from pydantic import JsonValue, TypeAdapter
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from athlete_platform.application.jobs import (
    FailureCode,
    JobConflict,
    JobLease,
    JobNotFound,
    JobState,
    JobView,
    QueueFull,
    QueueLimits,
)
from athlete_platform.domain.identity import VerifiedIdentity
from athlete_platform.infrastructure.database import transaction
from athlete_platform.infrastructure.job_models import DailyJobAttempts, ExtractionJob, JobAttempt
from athlete_platform.infrastructure.models import Athlete, User

payload_adapter = TypeAdapter(dict[str, JsonValue])
IDEMPOTENCY_CONSTRAINT = "uq_extraction_jobs_athlete_id"


def validated_payload(payload: dict[str, JsonValue]) -> tuple[dict[str, JsonValue], str]:
    value = payload_adapter.validate_python(payload)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > 65536:
        raise ValueError("Job payload exceeds 64 KiB")
    return value, hashlib.sha256(encoded).hexdigest()


def is_idempotency_violation(error: IntegrityError) -> bool:
    diag = getattr(error.orig, "diag", None)
    return (
        getattr(error.orig, "sqlstate", None) == "23505"
        and getattr(diag, "constraint_name", None) == IDEMPOTENCY_CONSTRAINT
    )


def view(job: ExtractionJob) -> JobView:
    return JobView(
        id=job.id,
        athlete_id=job.athlete_id,
        state=JobState(job.state),
        attempts=job.attempts,
        attempt_limit=job.attempt_limit,
        available_at=job.available_at,
        lease_expires_at=job.lease_expires_at,
        error_code=FailureCode(job.error_code) if job.error_code else None,
        result=job.result,
    )


def clock(session: Session) -> datetime:
    value: datetime = session.execute(select(func.clock_timestamp())).scalar_one()
    return value.astimezone(UTC)


class PostgresJobs:
    def __init__(self, factory: sessionmaker[Session], limits: QueueLimits | None = None) -> None:
        self.factory = factory
        self.limits = limits or QueueLimits()

    def _owned(
        self, session: Session, identity: VerifiedIdentity, job_id: UUID, *, lock: bool = False
    ) -> ExtractionJob:
        query = (
            select(ExtractionJob)
            .join(User, ExtractionJob.owner_user_id == User.id)
            .where(
                ExtractionJob.id == job_id,
                User.issuer == identity.issuer,
                User.subject == identity.subject,
            )
        )
        if lock:
            query = query.with_for_update(of=ExtractionJob)
        job = session.scalar(query)
        if job is None:
            raise JobNotFound
        return job

    def enqueue(
        self,
        identity: VerifiedIdentity,
        athlete_id: UUID,
        key: str,
        payload: dict[str, JsonValue],
    ) -> JobView:
        if not key.strip() or len(key) > 128:
            raise ValueError("Idempotency key must contain 1–128 characters")
        payload, digest = validated_payload(payload)
        try:
            return self._insert(identity, athlete_id, key, payload, digest)
        except IntegrityError as error:
            if not is_idempotency_violation(error):
                raise
        # A concurrent enqueue committed this key first; resolve against its stored input.
        with transaction(self.factory) as session:
            existing = self._existing(session, athlete_id, key, digest)
            if existing is None:
                raise JobNotFound
            return existing

    def _insert(
        self,
        identity: VerifiedIdentity,
        athlete_id: UUID,
        key: str,
        payload: dict[str, JsonValue],
        digest: str,
    ) -> JobView:
        with transaction(self.factory) as session:
            athlete = session.scalar(
                select(Athlete)
                .join(User, Athlete.owner_user_id == User.id)
                .where(
                    Athlete.id == athlete_id,
                    User.issuer == identity.issuer,
                    User.subject == identity.subject,
                )
                .with_for_update(of=Athlete)
            )
            if athlete is None:
                raise JobNotFound
            existing = self._existing(session, athlete_id, key, digest)
            if existing is not None:
                return existing
            active = session.scalar(
                select(func.count())
                .select_from(ExtractionJob)
                .where(
                    ExtractionJob.athlete_id == athlete_id,
                    ExtractionJob.state.in_(["queued", "running", "deferred"]),
                )
            )
            if active is not None and active >= self.limits.active_jobs_per_athlete:
                raise QueueFull
            job = ExtractionJob(
                id=uuid4(),
                athlete_id=athlete_id,
                owner_user_id=athlete.owner_user_id,
                idempotency_key=key,
                input_hash=digest,
                payload=payload,
                state="queued",
                attempts=0,
                attempt_limit=self.limits.max_attempts,
                available_at=clock(session),
            )
            session.add(job)
            session.flush()
            return view(job)

    def _existing(
        self, session: Session, athlete_id: UUID, key: str, digest: str
    ) -> JobView | None:
        existing = session.scalar(
            select(ExtractionJob).where(
                ExtractionJob.athlete_id == athlete_id,
                ExtractionJob.idempotency_key == key,
            )
        )
        if existing is None:
            return None
        if existing.input_hash != digest:
            raise JobConflict
        return view(existing)

    def get(self, identity: VerifiedIdentity, job_id: UUID) -> JobView:
        with transaction(self.factory) as session:
            return view(self._owned(session, identity, job_id))

    def history(self, identity: VerifiedIdentity, job_id: UUID) -> list[dict[str, JsonValue]]:
        with transaction(self.factory) as session:
            self._owned(session, identity, job_id)
            return [
                {
                    "number": row.number,
                    "outcome": row.outcome,
                    "error_code": row.error_code,
                    "started_at": row.started_at.isoformat(),
                    "finished_at": row.finished_at.isoformat() if row.finished_at else None,
                }
                for row in session.scalars(
                    select(JobAttempt)
                    .where(JobAttempt.job_id == job_id)
                    .order_by(JobAttempt.number)
                )
            ]

    def retry(self, identity: VerifiedIdentity, job_id: UUID, expected_attempt: int) -> JobView:
        with transaction(self.factory) as session:
            job = self._owned(session, identity, job_id, lock=True)
            if job.attempts != expected_attempt:
                raise JobConflict
            if job.state in ("queued", "deferred"):
                return view(job)
            if job.state != "failed":
                raise JobConflict
            session.execute(
                select(Athlete.id).where(Athlete.id == job.athlete_id).with_for_update()
            )
            active = session.scalar(
                select(func.count())
                .select_from(ExtractionJob)
                .where(
                    ExtractionJob.athlete_id == job.athlete_id,
                    ExtractionJob.state.in_(["queued", "running", "deferred"]),
                )
            )
            if active is not None and active >= self.limits.active_jobs_per_athlete:
                raise QueueFull
            job.state = "queued"
            job.error_code = None
            job.attempt_limit = job.attempts + self.limits.max_attempts
            job.available_at = clock(session)
            job.lease_token = None
            job.lease_expires_at = None
            return view(job)

    def claim(self) -> JobLease | None:
        # Bound cleanup/deferral work too; a later worker can continue large backlogs.
        for _ in range(100):
            with transaction(self.factory) as session:
                now = clock(session)
                job = session.scalar(
                    select(ExtractionJob)
                    .where(
                        or_(
                            and_(
                                ExtractionJob.state.in_(["queued", "deferred"]),
                                ExtractionJob.available_at <= now,
                            ),
                            and_(
                                ExtractionJob.state == "running",
                                ExtractionJob.lease_expires_at <= now,
                            ),
                        )
                    )
                    .order_by(ExtractionJob.available_at, ExtractionJob.id)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                if job is None:
                    return None
                # All quota claims for one athlete serialize, even across different jobs.
                session.execute(
                    select(Athlete.id).where(Athlete.id == job.athlete_id).with_for_update()
                )
                now = clock(session)
                if job.state == "running":
                    attempt = session.get(JobAttempt, (job.id, job.attempts))
                    assert attempt is not None
                    attempt.outcome, attempt.finished_at = "expired", now
                if job.attempts >= job.attempt_limit:
                    job.state, job.error_code = "failed", FailureCode.ATTEMPT_LIMIT.value
                    job.lease_expires_at = None
                    continue
                quota = session.get(DailyJobAttempts, (job.athlete_id, now.date()))
                if quota is None:
                    quota = DailyJobAttempts(athlete_id=job.athlete_id, day=now.date(), count=0)
                    session.add(quota)
                if quota.count >= self.limits.daily_attempts_per_athlete:
                    job.state = "deferred"
                    job.available_at = datetime.combine(now.date() + timedelta(days=1), time(), UTC)
                    job.lease_expires_at = None
                    continue
                quota.count += 1
                job.attempts += 1
                job.state, job.error_code = "running", None
                job.lease_token = uuid4()
                job.lease_expires_at = now + timedelta(seconds=self.limits.lease_seconds)
                session.add(
                    JobAttempt(
                        job_id=job.id,
                        number=job.attempts,
                        athlete_id=job.athlete_id,
                        lease_token=job.lease_token,
                        started_at=now,
                        outcome="running",
                    )
                )
                session.flush()
                result = JobLease(job=view(job), token=job.lease_token, payload=job.payload)
            return result
        return None

    def _live(self, job: ExtractionJob | None, lease: JobLease, now: datetime) -> bool:
        return bool(
            job is not None
            and job.state == "running"
            and job.lease_token == lease.token
            and job.lease_expires_at is not None
            and job.lease_expires_at > now
        )

    def heartbeat(self, lease: JobLease) -> bool:
        with transaction(self.factory) as session:
            job = session.get(ExtractionJob, lease.job.id, with_for_update=True)
            now = clock(session)
            if not self._live(job, lease, now):
                return False
            assert job is not None
            job.lease_expires_at = now + timedelta(seconds=self.limits.lease_seconds)
            return True

    def complete(self, lease: JobLease, result: dict[str, JsonValue]) -> bool:
        result, _ = validated_payload(result)
        with transaction(self.factory) as session:
            job = session.get(ExtractionJob, lease.job.id, with_for_update=True)
            if job is not None and job.state == "succeeded" and job.lease_token == lease.token:
                return job.result == result
            now = clock(session)
            if not self._live(job, lease, now):
                return False
            assert job is not None
            attempt = session.get(JobAttempt, (job.id, job.attempts))
            assert attempt is not None
            job.state, job.result, job.error_code = "succeeded", result, None
            job.lease_expires_at = None
            attempt.outcome, attempt.finished_at = "succeeded", now
            return True

    def fail(self, lease: JobLease, code: FailureCode, *, retryable: bool) -> bool:
        with transaction(self.factory) as session:
            job = session.get(ExtractionJob, lease.job.id, with_for_update=True)
            now = clock(session)
            if not self._live(job, lease, now):
                return False
            assert job is not None
            attempt = session.get(JobAttempt, (job.id, job.attempts))
            assert attempt is not None
            job.error_code = code.value
            job.lease_expires_at = None
            if retryable and job.attempts < job.attempt_limit:
                job.state = "deferred"
                delay = min(5 * 2 ** min(job.attempts - 1, 10), 300) + random.uniform(0, 1)
                job.available_at = now + timedelta(seconds=delay)
            else:
                job.state = "failed"
            attempt.outcome, attempt.finished_at, attempt.error_code = "failed", now, code.value
            return True
