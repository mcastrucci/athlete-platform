"""Confirmations and the canonical sessions they materialize; written only on confirmation."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from athlete_platform.infrastructure.database import Base


class SourceConfirmation(Base):
    __tablename__ = "source_confirmations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "athlete_id"], ["sources.id", "sources.athlete_id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["source_id", "interpretation_version"],
            ["interpretation_revisions.source_id", "interpretation_revisions.version"],
        ),
        UniqueConstraint("source_id", "athlete_id"),
    )
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    athlete_id: Mapped[UUID]
    interpretation_version: Mapped[int]
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CanonicalSession(Base):
    __tablename__ = "canonical_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "athlete_id"],
            ["source_confirmations.source_id", "source_confirmations.athlete_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("source_id", "position"),
        CheckConstraint("position >= 0", name="nonnegative_position"),
        CheckConstraint("status IN ('planned','performed')", name="valid_status"),
        CheckConstraint(
            "session_rpe IS NULL OR session_rpe BETWEEN 0 AND 10", name="valid_session_rpe"
        ),
        CheckConstraint("duration_value IS NULL OR duration_value >= 0", name="valid_duration"),
        CheckConstraint(
            "(duration_value IS NULL) = (duration_unit IS NULL)", name="duration_has_unit"
        ),
        Index("ix_canonical_sessions_athlete_date", "athlete_id", "session_date"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    athlete_id: Mapped[UUID]
    source_id: Mapped[UUID]
    position: Mapped[int]
    status: Mapped[str]
    session_date: Mapped[date]
    title: Mapped[str | None]
    date_text: Mapped[str | None]
    duration_value: Mapped[float | None] = mapped_column(Float)
    duration_unit: Mapped[str | None]
    session_rpe: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None]
    ambiguities: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)


class CanonicalSessionItem(Base):
    __tablename__ = "canonical_session_items"
    __table_args__ = (
        ForeignKeyConstraint(["session_id"], ["canonical_sessions.id"], ondelete="CASCADE"),
        CheckConstraint("position >= 0", name="nonnegative_position"),
        CheckConstraint(
            "kind IN ('strength','conditioning','skill','mobility','other','unknown')",
            name="valid_kind",
        ),
        CheckConstraint(
            "(sets IS NULL OR sets >= 0) AND (reps IS NULL OR reps >= 0) "
            "AND (rounds IS NULL OR rounds >= 0)",
            name="nonnegative_counts",
        ),
        CheckConstraint("rpe IS NULL OR rpe BETWEEN 0 AND 10", name="valid_rpe"),
        CheckConstraint(
            "(load_value IS NULL OR load_value >= 0) "
            "AND (duration_value IS NULL OR duration_value >= 0) "
            "AND (distance_value IS NULL OR distance_value >= 0)",
            name="nonnegative_quantities",
        ),
        CheckConstraint(
            "(load_value IS NULL) = (load_unit IS NULL) "
            "AND (duration_value IS NULL) = (duration_unit IS NULL) "
            "AND (distance_value IS NULL) = (distance_unit IS NULL)",
            name="quantities_have_units",
        ),
    )
    session_id: Mapped[UUID] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column(primary_key=True)
    exercise_text: Mapped[str]
    exercise_id: Mapped[str | None]
    kind: Mapped[str]
    prescription_text: Mapped[str | None]
    sets: Mapped[int | None]
    reps: Mapped[int | None]
    rounds: Mapped[int | None]
    load_value: Mapped[float | None] = mapped_column(Float)
    load_unit: Mapped[str | None]
    duration_value: Mapped[float | None] = mapped_column(Float)
    duration_unit: Mapped[str | None]
    distance_value: Mapped[float | None] = mapped_column(Float)
    distance_unit: Mapped[str | None]
    rpe: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None]
    ambiguities: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
