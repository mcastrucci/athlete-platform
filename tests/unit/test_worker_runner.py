"""Runner mapping of handler errors to persisted failure codes, without a database."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import JsonValue

from athlete_platform.application.jobs import FailureCode, JobLease, JobState, JobView
from athlete_platform.worker.runner import InvalidOutputError, run_worker


class RecordingQueue:
    def __init__(self, attempts: int) -> None:
        job = JobView(
            id=uuid4(),
            athlete_id=uuid4(),
            state=JobState.RUNNING,
            attempts=attempts,
            attempt_limit=3,
            available_at=datetime.now(UTC),
            lease_expires_at=None,
            error_code=None,
            result=None,
        )
        self.leases = [JobLease(job=job, token=uuid4(), payload={})]
        self.failures: list[tuple[FailureCode, bool]] = []

    def claim(self) -> JobLease | None:
        return self.leases.pop() if self.leases else None

    def heartbeat(self, lease: JobLease) -> bool:
        return True

    def complete(self, lease: JobLease, result: dict[str, JsonValue]) -> bool:
        return True

    def fail(self, lease: JobLease, code: FailureCode, *, retryable: bool) -> bool:
        self.failures.append((code, retryable))
        return True


def reject(lease: JobLease, renew: object) -> dict[str, JsonValue]:
    raise InvalidOutputError


@pytest.mark.parametrize("attempts,retryable", [(1, True), (2, False), (3, False)])
def test_invalid_output_is_retried_only_after_the_first_attempt(
    attempts: int, retryable: bool
) -> None:
    queue = RecordingQueue(attempts)
    assert run_worker(queue, reject) == 1
    assert queue.failures == [(FailureCode.INVALID_OUTPUT, retryable)]
