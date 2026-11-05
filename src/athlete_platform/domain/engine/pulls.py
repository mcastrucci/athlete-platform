"""Pull-repetition budget: dose helpers, weekly totals, rolling 7-day windows, evaluation.

Ported from the pinned baseline athlete@3f5897c (see docs/engineering/engine.md). Everything here
is a pure function of its arguments; nothing reads the wall clock.

Core rule: a missing or invalid dose is *unknown*, never zero. Unknown doses are reported next to
the known total and turn an otherwise passing check into ``manual``. Only an explicit
``completed_reps=0`` is a confirmed zero.
"""

import datetime
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .model import (
    Block,
    Catalog,
    PullCheckResult,
    PullConstraint,
    Quantity,
    Session,
    valid_count,
)

#: Exercise id of composite conditioning blocks. Its dose comes from components, never from
#: ``sets`` and ``reps``.
METCON_ID = "metcon"

WINDOW_DAYS = 7

#: Identifies the engine rules. The evaluation manifest records it; bump it when the engine or the
#: canonical-session adapter changes what an evaluation computes.
ENGINE_VERSION = "pull-budget-v1"


# ---------------------------------------------------------------- dose helpers


def iso_week(day: datetime.date) -> str:
    """Return the ISO week label of ``day`` as ``YYYY-Www``."""
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def iso_week_bounds(day: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Return the Monday and Sunday of the ISO week containing ``day``."""
    monday = day - datetime.timedelta(days=day.weekday())
    return monday, monday + datetime.timedelta(days=6)


def component_ids(block: Block) -> list[str]:
    """Return the exercise ids of the block's components."""
    return [component.exercise for component in block.components]


def set_count(block: Block) -> int:
    """Return the number of sets: the length of a set list, the integer count, or 0 when absent.

    Raises ``ValueError`` for a boolean, negative or fractional count.
    """
    sets = block.sets
    if sets is None:
        return 0
    if isinstance(sets, tuple):
        return len(sets)
    if isinstance(sets, bool) or not isinstance(sets, int) or sets < 0:
        raise ValueError(f"invalid set count: {sets!r}")
    return sets


def rep_count(block: Block) -> float | None:
    """Return the repetitions of the structured dose, or ``None`` when it is unknown.

    A set list sums its sets' reps; an integer ``sets`` multiplies by ``reps``. Any missing
    (``None``) or invalid rep makes the whole count unknown.

    Intentional difference from athlete@3f5897c: the baseline raised ``KeyError`` on a set record
    without reps and summed invalid values as they were; here both yield ``None``.
    """
    sets = block.sets
    if isinstance(sets, tuple):
        total = 0.0
        for record in sets:
            if not valid_count(record.reps):
                return None
            total += record.reps
        return total
    if (
        isinstance(sets, int)
        and not isinstance(sets, bool)
        and sets >= 0
        and valid_count(block.reps)
    ):
        return sets * block.reps
    return None


def quantity_errors(session: Session) -> list[str]:
    """List invalid recorded quantities (negative, boolean, NaN, infinite) in a session.

    ``None`` is not an error: it means "not reported". Invalid values never reduce a total; the
    engine treats them as unknown, and this validator lets callers reject them at the boundary.
    """
    errors: list[str] = []

    def check(where: str, name: str, value: Quantity | float) -> None:
        if value is not None and not valid_count(value):
            errors.append(f"{where}: {name} invalid ({value!r})")

    for block in session.blocks:
        where = f"{session.date.isoformat()} {block.exercise}"
        if isinstance(block.sets, tuple):
            for record in block.sets:
                check(where, "sets[].reps", record.reps)
        elif block.sets is not None and (
            isinstance(block.sets, bool) or not isinstance(block.sets, int) or block.sets < 0
        ):
            errors.append(f"{where}: sets invalid ({block.sets!r})")
        check(where, "reps", block.reps)
        check(where, "rounds", block.rounds)
        for component, reps in block.caps.items():
            check(where, f"caps.{component}", reps)
        for component_ in block.components:
            check(where, f"{component_.exercise}.reps", component_.reps)
            check(where, f"{component_.exercise}.completed_reps", component_.completed_reps)
    return errors


def pull_constraints(constraints: Iterable[PullConstraint]) -> list[PullConstraint]:
    """Return the active constraints that cap at least one category."""
    return [c for c in constraints if c.active and c.weekly_caps]


# ---------------------------------------------------------------- per-session dose


def _category(catalog: Catalog, exercise_id: str) -> str | None:
    entry = catalog.get(exercise_id)
    return entry.pull_category if entry is not None else None


def _logged_reps(block: Block) -> float | None:
    """Sum the reps of a logged set list; ``None`` unless every set reports a valid count."""
    sets = block.sets
    if not isinstance(sets, tuple) or not sets:
        return None
    return rep_count(block)


def session_pulls(
    session: Session, catalog: Catalog, *, logged: bool
) -> tuple[dict[str, float], set[str]]:
    """Return pull reps per category and the categories whose dose is unknown in one session.

    Plan (``logged=False``): sets x reps of pull blocks plus the ``caps`` reserved for components
    of open formats. Conditional components are not planned exposure.

    Log (``logged=True``): the performed sets and, for metcon components, ``completed_reps`` or
    complete rounds x reps per round. A conditional component without reported reps was not done.
    Missing or invalid data is never invented or subtracted: the category becomes unknown.

    Intentional differences from athlete@3f5897c: a planned block whose dose is incomplete (no
    structured sets and reps, or an invalid reserved cap) makes its category unknown instead of
    adding zero, and so does a planned non-conditional component with a pull category that has no
    reserved cap (an unbounded open format). The baseline relied on separate plan checks to fail
    such blocks.
    """
    known: dict[str, float] = {}
    unknown: set[str] = set()

    def add(category: str, reps: float) -> None:
        known[category] = known.get(category, 0) + reps

    for block in session.blocks:
        category = _category(catalog, block.exercise)
        if category and block.exercise != METCON_ID:
            reps = _logged_reps(block) if logged else rep_count(block)
            if reps is None:
                unknown.add(category)
            else:
                add(category, reps)
        if not logged:
            for component, cap in block.caps.items():
                cap_category = _category(catalog, component)
                if cap_category is None:
                    continue
                if valid_count(cap):
                    add(cap_category, cap)
                else:
                    unknown.add(cap_category)
            for planned_component in block.components:
                uncapped_category = _category(catalog, planned_component.exercise)
                if (
                    uncapped_category
                    and not planned_component.conditional
                    and planned_component.exercise not in block.caps
                ):
                    unknown.add(uncapped_category)  # an open format without a reserved cap
            continue
        for component_ in block.components:
            component_category = _category(catalog, component_.exercise)
            if component_category is None:
                continue
            if component_.completed_reps is not None:
                if not valid_count(component_.completed_reps):
                    unknown.add(component_category)
                    continue
                add(component_category, component_.completed_reps)
            elif component_.conditional:
                continue
            elif valid_count(block.rounds) and valid_count(component_.reps):
                add(component_category, block.rounds * component_.reps)
            else:
                unknown.add(component_category)
    return known, unknown


# ---------------------------------------------------------------- weekly and rolling totals


@dataclass(frozen=True)
class PullTotals:
    """Known reps per category and the dates whose dose is unknown per category."""

    known: Mapping[str, float]
    unknown_dates: Mapping[str, tuple[datetime.date, ...]]


def _check_week(week_start: datetime.date, week_end: datetime.date) -> None:
    if week_end < week_start:
        raise ValueError("week_end must not be before week_start")


def _daily(
    sessions: Iterable[Session], catalog: Catalog
) -> tuple[dict[datetime.date, dict[str, float]], dict[str, set[datetime.date]]]:
    daily: dict[datetime.date, dict[str, float]] = {}
    unknown: dict[str, set[datetime.date]] = {}
    for session in sessions:
        known, missing = session_pulls(session, catalog, logged=session.performed)
        for category, reps in known.items():
            day = daily.setdefault(session.date, {})
            day[category] = day.get(category, 0) + reps
        for category in missing:
            unknown.setdefault(category, set()).add(session.date)
    return daily, unknown


def select_sessions(
    sessions: Iterable[Session],
    *,
    first: datetime.date,
    week_start: datetime.date,
    week_end: datetime.date,
) -> list[Session]:
    """Select the sessions that make up the evaluated span, counting each day once.

    Performed sessions dated from ``first`` to ``week_end`` are kept. Planned sessions are kept
    only inside the week and only on dates without a performed session: the log replaces the plan
    for that day, so a planned session and its log are never added together. A planned session
    before the week never happened and is ignored.

    Extension over athlete@3f5897c, which used only the plan inside the week.
    """
    performed = [s for s in sessions if s.performed and first <= s.date <= week_end]
    logged_days = {s.date for s in performed}
    planned = [
        s
        for s in sessions
        if not s.performed and week_start <= s.date <= week_end and s.date not in logged_days
    ]
    return performed + planned


def weekly_pulls(
    sessions: Iterable[Session],
    catalog: Catalog,
    *,
    week_start: datetime.date,
    week_end: datetime.date,
) -> PullTotals:
    """Total pull reps per category over the sessions dated inside ``[week_start, week_end]``.

    Each date counts once: a date with a performed session uses the log and ignores that date's
    planned sessions; a date with only plan uses the plan.
    """
    _check_week(week_start, week_end)
    inside = select_sessions(sessions, first=week_start, week_start=week_start, week_end=week_end)
    daily, unknown = _daily(inside, catalog)
    totals: dict[str, float] = {}
    for per_category in daily.values():
        for category, reps in per_category.items():
            totals[category] = totals.get(category, 0) + reps
    return PullTotals(
        known=totals,
        unknown_dates={category: tuple(sorted(days)) for category, days in unknown.items()},
    )


@dataclass(frozen=True)
class RollingPull:
    """The worst 7-day window of a category.

    ``value`` is the highest known total of any 7-day span ending inside the week (the earliest
    such end wins ties), ``end`` the last day of that span, and ``unknown_dates`` the days between
    six days before the week and its end whose dose is unknown.
    """

    value: float
    end: datetime.date
    unknown_dates: tuple[datetime.date, ...]


def rolling_pulls(
    sessions: Iterable[Session],
    catalog: Catalog,
    constraints: Iterable[PullConstraint],
    *,
    week_start: datetime.date,
    week_end: datetime.date,
) -> dict[str, RollingPull]:
    """Return, per regulated category, the maximum pull reps in any 7-day window ending in the week.

    Performed sessions before the week contribute what was logged; each day inside the week
    contributes its log if it has a performed session, else its plan. A window is the 7 days
    ending on a day from ``week_start`` to ``week_end``.
    """
    _check_week(week_start, week_end)
    first = week_start - datetime.timedelta(days=WINDOW_DAYS - 1)
    selected = select_sessions(sessions, first=first, week_start=week_start, week_end=week_end)
    daily, unknown = _daily(selected, catalog)
    regulated = {cat for c in pull_constraints(constraints) for cat in c.weekly_caps}
    result: dict[str, RollingPull] = {}
    for category in regulated:
        best_value: float = 0
        best_end = week_start
        for offset in range((week_end - week_start).days + 1):
            end = week_start + datetime.timedelta(days=offset)
            total = sum(
                daily.get(end - datetime.timedelta(days=k), {}).get(category, 0)
                for k in range(WINDOW_DAYS)
            )
            if total > best_value:
                best_value, best_end = total, end
        missing = tuple(sorted(d for d in unknown.get(category, set()) if first <= d <= week_end))
        result[category] = RollingPull(best_value, best_end, missing)
    return result


# ---------------------------------------------------------------- evaluation


def _status(value: float, cap: float, unknown: Sequence[datetime.date]) -> str:
    if value > cap:
        return "fail"
    return "manual" if unknown else "ok"


def _days(days: Sequence[datetime.date]) -> str:
    return ", ".join(d.isoformat() for d in days)


def evaluate_pull_constraints(
    sessions: Sequence[Session],
    catalog: Catalog,
    constraints: Sequence[PullConstraint],
    *,
    week_start: datetime.date,
    week_end: datetime.date,
) -> list[PullCheckResult]:
    """Check every active constraint against the week and against the rolling 7-day windows.

    Returns one ``weekly`` result per (constraint id, category), followed by one ``rolling``
    result per (constraint id, category) when days outside the week contribute to the worst
    window or when any dose is unknown. Constraints sharing a category get separate results with
    their own id and cap, so a manual review of one never covers another.

    Status: ``fail`` when the known total exceeds the cap (unknown doses can only add to it),
    otherwise ``manual`` when any dose is unknown (an unknown dose cannot certify a cap), else
    ``ok``.
    """
    _check_week(week_start, week_end)
    active = pull_constraints(constraints)
    weekly = weekly_pulls(sessions, catalog, week_start=week_start, week_end=week_end)
    rolling = rolling_pulls(sessions, catalog, active, week_start=week_start, week_end=week_end)
    results: list[PullCheckResult] = []
    for constraint in active:
        for category, cap in constraint.weekly_caps.items():
            value = weekly.known.get(category, 0)
            unknown = weekly.unknown_dates.get(category, ())
            status = _status(value, cap, unknown)
            detail = ""
            if status == "manual":
                detail = (
                    f"unknown pull dose on {_days(unknown)}: complete the data or review by hand"
                )
            results.append(
                PullCheckResult(
                    constraint_id=constraint.id,
                    category=category,
                    kind="weekly",
                    cap=cap,
                    value=value,
                    window_start=week_start,
                    window_end=week_end,
                    status=status,
                    unknown_dates=unknown,
                    detail=detail,
                )
            )
    for constraint in active:
        for category, cap in constraint.weekly_caps.items():
            window = rolling[category]
            if not (window.value > weekly.known.get(category, 0) or window.unknown_dates):
                continue  # days before the week add nothing and nothing is unknown
            status = _status(window.value, cap, window.unknown_dates)
            if status == "fail":
                detail = "counts what was logged before the week"
            elif status == "manual":
                detail = (
                    f"unknown pull dose on {_days(window.unknown_dates)}: "
                    "complete the data or review by hand"
                )
            else:
                detail = "counts what was logged before the week"
            results.append(
                PullCheckResult(
                    constraint_id=constraint.id,
                    category=category,
                    kind="rolling",
                    cap=cap,
                    value=window.value,
                    window_start=window.end - datetime.timedelta(days=WINDOW_DAYS - 1),
                    window_end=window.end,
                    status=status,
                    unknown_dates=window.unknown_dates,
                    detail=detail,
                )
            )
    return results
