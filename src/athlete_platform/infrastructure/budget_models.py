"""Monthly model-spend ledger: a lock row per UTC month and one entry per job and month."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from athlete_platform.infrastructure.database import Base

USD = Numeric(14, 6)


class BudgetMonth(Base):
    """Exists to be row-locked: every reservation of a month serializes on its row."""

    __tablename__ = "model_budget_months"
    __table_args__ = (CheckConstraint("extract(day FROM month) = 1", name="first_of_month"),)
    month: Mapped[date] = mapped_column(Date(), primary_key=True)


class ModelSpend(Base):
    """Spend of one job in one month.

    `spent_usd` is the cost of calls whose usage was reported. `reserved_usd` is an
    outstanding hold for a call that has not been reconciled; it counts against the budget
    until the call is reconciled or released. `uncertain_usd` holds that were superseded by a
    later attempt of the same job before being reconciled: the earlier call may have been
    billed, so they keep counting. Nothing clears them automatically.
    """

    __tablename__ = "model_spend"
    __table_args__ = (
        CheckConstraint(
            "reserved_usd >= 0 AND spent_usd >= 0 AND uncertain_usd >= 0",
            name="nonnegative_amounts",
        ),
        CheckConstraint("input_tokens >= 0 AND output_tokens >= 0", name="nonnegative_tokens"),
        Index("ix_model_spend_month", "month"),
    )
    job_id: Mapped[UUID] = mapped_column(primary_key=True)
    month: Mapped[date] = mapped_column(
        Date(), ForeignKey("model_budget_months.month"), primary_key=True
    )
    provider: Mapped[str]
    model: Mapped[str]
    reserved_usd: Mapped[Decimal] = mapped_column(USD)
    spent_usd: Mapped[Decimal] = mapped_column(USD)
    uncertain_usd: Mapped[Decimal] = mapped_column(USD, server_default="0")
    input_tokens: Mapped[int] = mapped_column(Integer())
    output_tokens: Mapped[int] = mapped_column(Integer())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
