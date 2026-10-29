"""Durable jobs, ownership constraints, attempt history, and UTC quota counters."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from athlete_platform.infrastructure.database import Base


class ExtractionJob(Base):
    __tablename__ = "extraction_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["athlete_id", "owner_user_id"],
            ["athletes.id", "athletes.owner_user_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("athlete_id", "idempotency_key"),
        UniqueConstraint("id", "athlete_id"),
        CheckConstraint(
            "state IN ('queued','running','deferred','succeeded','failed')", name="valid_state"
        ),
        CheckConstraint("attempts >= 0 AND attempt_limit >= 1", name="valid_attempts"),
        CheckConstraint(
            "state != 'running' OR (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="running_has_lease",
        ),
        Index("ix_extraction_jobs_claim", "state", "available_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    athlete_id: Mapped[UUID]
    owner_user_id: Mapped[UUID]
    idempotency_key: Mapped[str]
    input_hash: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state: Mapped[str]
    attempts: Mapped[int]
    attempt_limit: Mapped[int]
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None]
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobAttempt(Base):
    __tablename__ = "job_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "athlete_id"],
            ["extraction_jobs.id", "extraction_jobs.athlete_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("lease_token"),
        CheckConstraint("number >= 1", name="positive_number"),
        CheckConstraint(
            "outcome IN ('running','succeeded','failed','expired')", name="valid_outcome"
        ),
    )
    job_id: Mapped[UUID] = mapped_column(primary_key=True)
    number: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[UUID]
    lease_token: Mapped[UUID]
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str]
    error_code: Mapped[str | None]


class DailyJobAttempts(Base):
    __tablename__ = "daily_job_attempts"
    __table_args__ = (CheckConstraint("count >= 0", name="nonnegative_count"),)
    athlete_id: Mapped[UUID] = mapped_column(
        ForeignKey("athletes.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date(), primary_key=True)
    count: Mapped[int]
