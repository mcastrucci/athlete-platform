"""Contracts for the text-extraction queue; no provider or database dependency."""

from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DEFERRED = "deferred"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class FailureCode(StrEnum):
    TRANSIENT = "transient_failure"
    INVALID_OUTPUT = "invalid_output"
    UNCONFIGURED = "provider_unconfigured"
    HANDLER_ERROR = "handler_error"
    ATTEMPT_LIMIT = "attempt_limit"


class QueueLimits(BaseModel):
    model_config = ConfigDict(frozen=True)
    lease_seconds: int = Field(default=120, ge=1, le=3600)
    max_attempts: int = Field(default=3, ge=1, le=10)
    active_jobs_per_athlete: int = Field(default=100, ge=1, le=1000)
    daily_attempts_per_athlete: int = Field(default=50, ge=1, le=10000)


class JobView(BaseModel):
    id: UUID
    athlete_id: UUID
    state: JobState
    attempts: int
    attempt_limit: int
    available_at: datetime
    lease_expires_at: datetime | None
    error_code: FailureCode | None
    result: dict[str, JsonValue] | None


class JobLease(BaseModel):
    job: JobView
    token: UUID
    payload: dict[str, JsonValue]


class JobNotFound(Exception):
    """Job is absent or belongs to another athlete."""


class JobConflict(Exception):
    """Idempotency input or retry version conflicts with stored work."""


class QueueFull(Exception):
    """Athlete has reached the active submission limit."""


class WorkerQueue(Protocol):
    def claim(self) -> JobLease | None: ...
    def heartbeat(self, lease: JobLease) -> bool: ...
    def complete(self, lease: JobLease, result: dict[str, JsonValue]) -> bool: ...
    def fail(self, lease: JobLease, code: FailureCode, *, retryable: bool) -> bool: ...
