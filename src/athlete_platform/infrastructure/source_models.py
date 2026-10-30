"""Immutable owned sources and append-only interpretation revisions."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from athlete_platform.infrastructure.database import Base


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["athlete_id", "owner_user_id"],
            ["athletes.id", "athletes.owner_user_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["duplicate_of", "athlete_id"], ["sources.id", "sources.athlete_id"]),
        UniqueConstraint("athlete_id", "idempotency_key"),
        UniqueConstraint("id", "athlete_id"),
        CheckConstraint("kind = 'text'", name="known_kind"),
        CheckConstraint("char_length(text) BETWEEN 1 AND 20000", name="bounded_text"),
        CheckConstraint(
            "duplicate_status IN ('none','pending','kept','discarded')", name="valid_duplicate"
        ),
        CheckConstraint(
            "(duplicate_of IS NULL) = (duplicate_status = 'none')", name="duplicate_has_target"
        ),
        CheckConstraint(
            "interpretation_version IS NULL OR interpretation_version >= 1",
            name="positive_interpretation",
        ),
        Index("ix_sources_content", "athlete_id", "content_hash"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    athlete_id: Mapped[UUID]
    owner_user_id: Mapped[UUID]
    idempotency_key: Mapped[str]
    kind: Mapped[str]
    text: Mapped[str]
    content_hash: Mapped[str]
    duplicate_of: Mapped[UUID | None]
    duplicate_status: Mapped[str]
    interpretation_version: Mapped[int | None]
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class InterpretationRevision(Base):
    __tablename__ = "interpretation_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "athlete_id"], ["sources.id", "sources.athlete_id"], ondelete="CASCADE"
        ),
        UniqueConstraint("run_id"),
        CheckConstraint("version >= 1", name="positive_version"),
        CheckConstraint("schema_version = 1", name="known_schema"),
        CheckConstraint("origin IN ('extraction','correction')", name="valid_origin"),
        CheckConstraint(
            "(origin = 'extraction') = (run_id IS NOT NULL AND provenance IS NOT NULL)",
            name="extraction_has_provenance",
        ),
        CheckConstraint(
            "origin = 'extraction' OR based_on_version IS NOT NULL",
            name="correction_has_base",
        ),
        CheckConstraint(
            "based_on_version IS NULL OR based_on_version < version", name="earlier_base"
        ),
    )
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[UUID]
    origin: Mapped[str]
    based_on_version: Mapped[int | None]
    run_id: Mapped[UUID | None]
    provenance: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    schema_version: Mapped[int]
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
