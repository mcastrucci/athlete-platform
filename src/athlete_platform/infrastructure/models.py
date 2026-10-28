"""Relational ownership and append-only profile snapshots."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from athlete_platform.infrastructure.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("issuer", "subject"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    issuer: Mapped[str]
    subject: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Athlete(Base):
    __tablename__ = "athletes"
    __table_args__ = (
        UniqueConstraint("owner_user_id"),
        UniqueConstraint("id", "owner_user_id"),
        CheckConstraint("version >= 1", name="positive_version"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    version: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProfileRevision(Base):
    __tablename__ = "profile_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["athlete_id", "owner_user_id"], ["athletes.id", "athletes.owner_user_id"]
        ),
        CheckConstraint("version >= 1", name="positive_version"),
        CheckConstraint("schema_version = 1", name="known_schema"),
    )
    athlete_id: Mapped[UUID] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[UUID]
    schema_version: Mapped[int]
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
