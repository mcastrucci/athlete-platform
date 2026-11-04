"""Bounded runner; only a configured handler can execute extraction work."""

from collections.abc import Callable
from functools import partial
from time import monotonic

from pydantic import JsonValue

from athlete_platform.application.jobs import FailureCode, JobLease, WorkerQueue

Handler = Callable[[JobLease, Callable[[], bool]], dict[str, JsonValue]]


class TransientJobError(Exception):
    """A handler failed transiently; no private exception text is persisted."""


class InvalidOutputError(Exception):
    """A handler received output that failed validation; retried only on a first attempt."""


def run_worker(
    queue: WorkerQueue, handler: Handler, *, max_jobs: int = 10, max_seconds: float = 60
) -> int:
    """Drain eligible work, with no idle polling or transaction across the handler.

    Handlers must bound their own calls and renew leases before expiry. The runtime
    budget is checked between jobs; it cannot interrupt an in-flight Python call.
    """
    if not 1 <= max_jobs <= 1000 or not 0 < max_seconds <= 3600:
        raise ValueError("Use bounded positive worker limits")
    deadline = monotonic() + max_seconds
    processed = 0
    while processed < max_jobs and monotonic() < deadline:
        lease = queue.claim()
        if lease is None:
            break
        try:
            result = handler(lease, partial(queue.heartbeat, lease))
        except TransientJobError:
            queue.fail(lease, FailureCode.TRANSIENT, retryable=True)
        except InvalidOutputError:
            # Model output may vary between calls, so allow one more try after the first.
            queue.fail(lease, FailureCode.INVALID_OUTPUT, retryable=lease.job.attempts <= 1)
        except Exception:
            queue.fail(lease, FailureCode.HANDLER_ERROR, retryable=False)
        else:
            try:
                queue.complete(lease, result)
            except ValueError:
                queue.fail(lease, FailureCode.INVALID_OUTPUT, retryable=False)
        processed += 1
    return processed
