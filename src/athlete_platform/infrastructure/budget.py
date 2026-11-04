"""PostgreSQL model-spend gate: serialized per UTC month, idempotent per job.

Every reserve, reconcile, and release first locks the month's `model_budget_months` row, so
concurrent workers observe each other's committed holds and cannot together exceed the budget.
Each operation is one short transaction; none is open during a provider call.

Semantics per (job, month) entry:

- `reserve` places a new hold. A hold the job still has outstanding (its earlier call ended
  uncertain: timeout, connection error, 5xx) is moved into `uncertain_usd` first, never
  discarded, because that call may have been billed. The move is committed even when the new
  hold is refused. Amounts the job already spent also count, so a retry costs its new hold
  plus everything the job may already have cost.
- `reconcile` settles only the current hold: it adds the cost of the reported usage to
  `spent_usd` and clears the hold. With no outstanding hold it does nothing, so repeating it
  cannot double-count.
- `release` clears only the current hold. The caller may use it only when the provider is
  known not to have processed the request.
- Uncertain amounts are never released automatically. An operator can review `model_spend`
  rows with `uncertain_usd > 0` against the provider's usage report and lower them by hand.
"""

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from athlete_platform.application.budget import (
    BudgetExhausted,
    Reservation,
    usage_cost_usd,
)
from athlete_platform.application.extraction import ExtractionUsage
from athlete_platform.infrastructure.budget_models import BudgetMonth, ModelSpend
from athlete_platform.infrastructure.database import transaction

logger = logging.getLogger(__name__)

WARNING_LEVELS = (Decimal("0.5"), Decimal("0.8"))


def utc_now() -> datetime:
    return datetime.now(UTC)


def month_start(moment: datetime) -> date:
    utc = moment.astimezone(UTC)
    return date(utc.year, utc.month, 1)


class PostgresBudget:
    def __init__(
        self,
        factory: sessionmaker[Session],
        monthly_budget_usd: Decimal,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if monthly_budget_usd <= 0:
            raise ValueError("The monthly budget must be positive")
        self.factory = factory
        self.monthly_budget_usd = monthly_budget_usd
        self.clock = clock

    def _lock(self, session: Session, month: date) -> None:
        session.execute(insert(BudgetMonth).values(month=month).on_conflict_do_nothing())
        session.execute(
            select(BudgetMonth.month).where(BudgetMonth.month == month).with_for_update()
        )

    def _committed(
        self, session: Session, month: date, *, excluding: UUID | None = None
    ) -> Decimal:
        query = select(
            func.coalesce(
                func.sum(ModelSpend.reserved_usd + ModelSpend.spent_usd + ModelSpend.uncertain_usd),
                0,
            )
        ).where(ModelSpend.month == month)
        if excluding is not None:
            query = query.where(ModelSpend.job_id != excluding)
        return Decimal(session.execute(query).scalar_one())

    def _warn(self, month: date, before: Decimal, after: Decimal) -> None:
        for level in WARNING_LEVELS:
            threshold = self.monthly_budget_usd * level
            if before < threshold <= after:
                logger.warning(
                    "Model budget passed %d%% for %s: committed_usd=%s budget_usd=%s",
                    int(level * 100),
                    month.strftime("%Y-%m"),
                    after,
                    self.monthly_budget_usd,
                )

    def reserve(self, job_id: UUID, *, provider: str, model: str, usd: Decimal) -> Reservation:
        month = month_start(self.clock())
        exhausted = False
        with transaction(self.factory) as session:
            self._lock(session, month)
            others = self._committed(session, month, excluding=job_id)
            entry = session.get(ModelSpend, (job_id, month))
            spent = entry.spent_usd if entry is not None else Decimal(0)
            uncertain = entry.uncertain_usd if entry is not None else Decimal(0)
            previous = entry.reserved_usd if entry is not None else Decimal(0)
            before = others + spent + uncertain + previous
            # The previous hold stays counted as uncertain whether or not the new one fits.
            after = before + usd
            if after > self.monthly_budget_usd:
                exhausted = True
            if entry is None:
                if not exhausted:
                    session.add(
                        ModelSpend(
                            job_id=job_id,
                            month=month,
                            provider=provider,
                            model=model,
                            reserved_usd=usd,
                            spent_usd=Decimal(0),
                            uncertain_usd=Decimal(0),
                            input_tokens=0,
                            output_tokens=0,
                        )
                    )
            else:
                session.execute(
                    update(ModelSpend)
                    .where(ModelSpend.job_id == job_id, ModelSpend.month == month)
                    .values(
                        provider=provider,
                        model=model,
                        uncertain_usd=uncertain + previous,
                        reserved_usd=Decimal(0) if exhausted else usd,
                        updated_at=func.now(),
                    )
                )
            session.flush()
        if exhausted:
            raise BudgetExhausted
        self._warn(month, before, after)
        return Reservation(job_id=job_id, month=month, reserved_usd=usd)

    def reconcile(self, reservation: Reservation, usage: ExtractionUsage) -> None:
        cost = usage_cost_usd(usage)
        with transaction(self.factory) as session:
            self._lock(session, reservation.month)
            entry = session.get(ModelSpend, (reservation.job_id, reservation.month))
            if entry is None or entry.reserved_usd == 0:
                return
            before = self._committed(session, reservation.month)
            session.execute(
                update(ModelSpend)
                .where(
                    ModelSpend.job_id == reservation.job_id,
                    ModelSpend.month == reservation.month,
                )
                .values(
                    reserved_usd=0,
                    spent_usd=ModelSpend.spent_usd + cost,
                    input_tokens=ModelSpend.input_tokens + usage.input_tokens,
                    output_tokens=ModelSpend.output_tokens + usage.output_tokens,
                    provider=usage.provider,
                    model=usage.model,
                    updated_at=func.now(),
                )
            )
            after = self._committed(session, reservation.month)
        self._warn(reservation.month, before, after)

    def release(self, reservation: Reservation) -> None:
        with transaction(self.factory) as session:
            self._lock(session, reservation.month)
            session.execute(
                update(ModelSpend)
                .where(
                    ModelSpend.job_id == reservation.job_id,
                    ModelSpend.month == reservation.month,
                )
                .values(reserved_usd=0, updated_at=func.now())
            )

    def uncertain_usd(self, month: date) -> Decimal:
        """Holds superseded by a later attempt before reconciliation; for operators and tests."""
        with transaction(self.factory) as session:
            return Decimal(
                session.execute(
                    select(func.coalesce(func.sum(ModelSpend.uncertain_usd), 0)).where(
                        ModelSpend.month == month
                    )
                ).scalar_one()
            )

    def committed_usd(self, month: date) -> Decimal:
        """Spent + held + uncertain for a UTC month: the amount counted against the budget."""
        with transaction(self.factory) as session:
            return self._committed(session, month)

    def month_totals(self, month: date) -> tuple[Decimal, Decimal]:
        """Return (spent_usd, reserved_usd) for a UTC month; for operators and tests."""
        with transaction(self.factory) as session:
            spent, reserved = session.execute(
                select(
                    func.coalesce(func.sum(ModelSpend.spent_usd), 0),
                    func.coalesce(func.sum(ModelSpend.reserved_usd), 0),
                ).where(ModelSpend.month == month)
            ).one()
            return Decimal(spent), Decimal(reserved)
