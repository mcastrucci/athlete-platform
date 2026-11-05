"""Stored pull-budget evaluations: one immutable row per (athlete, week, manifest hash)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from athlete_platform.infrastructure.database import Base


class EvaluationRow(Base):
    __tablename__ = "evaluations"
    __table_args__ = (
        UniqueConstraint("athlete_id", "week", "manifest_hash"),
        CheckConstraint("week ~ '^[0-9]{4}-W[0-9]{2}$'", name="valid_week"),
        CheckConstraint("manifest_hash ~ '^[0-9a-f]{64}$'", name="valid_manifest_hash"),
        CheckConstraint("jsonb_typeof(manifest) = 'object'", name="manifest_is_object"),
        CheckConstraint("jsonb_typeof(findings) = 'array'", name="findings_is_array"),
        CheckConstraint("jsonb_typeof(assumptions) = 'array'", name="assumptions_is_array"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    athlete_id: Mapped[UUID] = mapped_column(ForeignKey("athletes.id"))
    week: Mapped[str]
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    manifest_hash: Mapped[str]
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    assumptions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
