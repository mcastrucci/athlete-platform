"""Evaluate confirmed canonical sessions against pull budgets; pure, no database dependency.

The adapter turns canonical sessions into engine inputs. Its rules, in short (the full text is in
docs/engineering/evaluation.md):

- ``performed`` sessions use log semantics and ``planned`` ones plan semantics. The engine picks
  one record per date, so a log replaces a plan.
- An item's exercise is its ``exercise_id`` (normalized, see ``normalize_exercise_id``). An item
  without an id, or with an id the catalog does not list, is *unclassified*: it is reported as an
  assumption, and because it could be a pull, its date is unknown for every pull category. The
  result is manual review, never a pass and never a silent zero.
- A missing sets, reps or rounds value stays unknown; only an explicit ``0`` is a zero. Load,
  duration and distance are never read: their units are never summed or converted.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from athlete_platform.application.evaluation import (
    Assumption,
    Contribution,
    Finding,
    span_bounds,
    week_bounds,
)
from athlete_platform.application.profiles import PullBudget
from athlete_platform.application.sessions import CanonicalItem, CanonicalSession
from athlete_platform.domain.engine import (
    CATALOG,
    CATALOG_VERSION,
    ENGINE_VERSION,
    METCON_ID,
    Block,
    Catalog,
    Component,
    PullCheckResult,
    PullConstraint,
    Session,
    SetRecord,
    evaluate_pull_constraints,
    normalize_exercise_id,
    pull_categories,
    pull_constraints,
    representative_exercise,
    select_sessions,
    session_pulls,
)


@dataclass(frozen=True)
class AdaptedSession:
    """An engine session with the canonical session it came from.

    ``positions[i]`` is the item position of ``session.blocks[i]``. An unclassified item yields
    one placeholder block per pull category, so a position can repeat.
    """

    source: CanonicalSession
    session: Session
    positions: tuple[int, ...]
    assumptions: tuple[Assumption, ...]


def _dose_block(item: CanonicalItem, exercise: str, *, performed: bool) -> tuple[Block, bool]:
    """Return the block of a classified item and whether its sets and rounds contradict."""
    sets, reps, rounds = item.sets, item.reps, item.rounds
    if sets is not None and rounds is not None:
        return Block(exercise), True  # "n sets of m rounds" is ambiguous: dose unknown
    if rounds is not None:
        if performed:
            if rounds == 0:
                completed: float | None = 0.0
            else:
                completed = rounds * reps if reps is not None else None
            component = Component(exercise, reps=reps, completed_reps=completed)
            return Block(METCON_ID, components=(component,), rounds=rounds), False
        caps: dict[str, float] = {}
        if rounds == 0:
            caps = {exercise: 0.0}
        elif reps is not None:
            caps = {exercise: float(rounds * reps)}
        return Block(METCON_ID, caps=caps, components=(Component(exercise, reps=reps),)), False
    if sets == 0 or reps == 0:
        # A confirmed zero. A logged empty set list means "no data" to the engine, so a zero is
        # one record of zero reps.
        if performed:
            return Block(exercise, sets=(SetRecord(0.0),)), False
        return Block(exercise, sets=0, reps=0.0), False
    if sets is None:
        return Block(exercise), False  # reps without a set count: not guessed as one set
    if performed:
        return Block(exercise, sets=(SetRecord(reps),) * sets), False
    return Block(exercise, sets=sets, reps=reps), False


def adapt_session(session: CanonicalSession, catalog: Catalog = CATALOG) -> AdaptedSession:
    """Convert one canonical session into an engine session plus its assumptions."""
    performed = session.status == "performed"
    categories = pull_categories(catalog)
    blocks: list[Block] = []
    positions: list[int] = []
    assumptions: list[Assumption] = []

    def note(code: str, detail: str, position: int) -> None:
        assumptions.append(
            Assumption(
                code=code,  # type: ignore[arg-type]
                detail=detail,
                session_id=session.id,
                item_position=position,
            )
        )

    for item in session.items:
        raw = item.exercise_id
        exercise = normalize_exercise_id(raw) if raw is not None else None
        entry = catalog.get(exercise) if exercise else None
        if entry is None:
            if raw is None:
                note(
                    "unclassified_item",
                    f"Item {item.exercise_text!r} has no exercise id; it could be a pull, so "
                    "the day is unknown for every pull category.",
                    item.position,
                )
            else:
                note(
                    "unknown_exercise_id",
                    f"Exercise id {raw!r} is not in the catalog; it could be a pull, so the day "
                    "is unknown for every pull category.",
                    item.position,
                )
            for category in categories:
                placeholder = representative_exercise(category, catalog)
                if placeholder is not None:
                    blocks.append(Block(placeholder))  # no dose: unknown for this category
                    positions.append(item.position)
            continue
        block, ambiguous = _dose_block(item, entry.id, performed=performed)
        if ambiguous and entry.pull_category is not None:
            note(
                "ambiguous_dose",
                f"Item {item.exercise_text!r} has both sets and rounds; its dose is unknown.",
                item.position,
            )
        blocks.append(block)
        positions.append(item.position)
    return AdaptedSession(
        source=session,
        session=Session(session.session_date, tuple(blocks), performed=performed),
        positions=tuple(positions),
        assumptions=tuple(assumptions),
    )


def adapt_sessions(
    sessions: Sequence[CanonicalSession], catalog: Catalog = CATALOG
) -> list[AdaptedSession]:
    return [adapt_session(session, catalog) for session in sessions]


def _constraints(budgets: Sequence[PullBudget]) -> list[PullConstraint]:
    return [PullConstraint(b.id, b.weekly_caps, b.active) for b in budgets]


def _finding(
    result: PullCheckResult,
    contributions: list[Contribution],
    *,
    catalogued: bool,
) -> Finding:
    status, detail = result.status, result.detail
    if not catalogued:
        status = "manual"
        detail = (
            f"No exercise in the catalog counts toward {result.category!r}, "
            "so nothing can certify this cap."
        )
    return Finding(
        constraint_id=result.constraint_id,
        category=result.category,
        kind=result.kind,  # type: ignore[arg-type]
        value=float(result.value),
        cap=float(result.cap),
        status=status,  # type: ignore[arg-type]
        window_start=result.window_start,
        window_end=result.window_end,
        unknown_dates=list(result.unknown_dates),
        detail=detail,
        contributions=contributions,
        catalog_version=CATALOG_VERSION,
        engine_version=ENGINE_VERSION,
    )


def _contributions(
    result: PullCheckResult, selected: Sequence[AdaptedSession], catalog: Catalog
) -> list[Contribution]:
    """Items that add known pulls inside the window, or have an unknown dose on an unknown day."""
    unknown_days = set(result.unknown_dates)
    found: list[Contribution] = []
    for adapted in selected:
        day = adapted.session.date
        in_window = result.window_start <= day <= result.window_end
        if not in_window and day not in unknown_days:
            continue
        logged = adapted.session.performed
        for block, position in zip(adapted.session.blocks, adapted.positions, strict=True):
            known, unknown = session_pulls(Session(day, (block,), logged), catalog, logged=logged)
            if result.category in unknown and day in unknown_days:
                found.append(
                    Contribution(
                        session_id=adapted.source.id,
                        item_position=position,
                        session_date=day,
                        pulls=None,
                    )
                )
            elif in_window and known.get(result.category, 0) > 0:
                found.append(
                    Contribution(
                        session_id=adapted.source.id,
                        item_position=position,
                        session_date=day,
                        pulls=float(known[result.category]),
                    )
                )
    found.sort(key=lambda c: (c.session_date, str(c.session_id), c.item_position))
    return found


def evaluate_week(
    sessions: Sequence[CanonicalSession],
    budgets: Sequence[PullBudget] | None,
    *,
    week: str,
    catalog: Catalog = CATALOG,
) -> tuple[list[Finding], list[Assumption]]:
    """Evaluate confirmed sessions against the budgets for an ISO week.

    ``sessions`` should cover ``span_bounds(week)``; sessions outside it are ignored by the
    engine. Returns the findings and the assumptions the result rests on.
    """
    week_start, week_end = week_bounds(week)
    if not budgets:
        return [], [
            Assumption(
                code="no_budgets_configured",
                detail="The profile has no pull budgets, so there is nothing to evaluate.",
            )
        ]
    constraints = _constraints(budgets)
    active = pull_constraints(constraints)
    if not active:
        return [], [
            Assumption(
                code="no_active_budgets",
                detail="Every pull budget in the profile is inactive or has no caps.",
            )
        ]
    known_categories = set(pull_categories(catalog))
    assumptions: list[Assumption] = []
    for constraint in active:
        for category in constraint.weekly_caps:
            if category not in known_categories:
                assumptions.append(
                    Assumption(
                        code="uncatalogued_budget_category",
                        detail=f"Budget {constraint.id!r} caps {category!r}, which no catalog "
                        f"exercise ({CATALOG_VERSION}) counts toward.",
                    )
                )

    adapted = adapt_sessions(sessions, catalog)
    engine_sessions = [a.session for a in adapted]
    results = evaluate_pull_constraints(
        engine_sessions, catalog, constraints, week_start=week_start, week_end=week_end
    )
    first, _ = span_bounds(week)
    chosen = {
        id(s)
        for s in select_sessions(
            engine_sessions, first=first, week_start=week_start, week_end=week_end
        )
    }
    selected = [a for a in adapted if id(a.session) in chosen]
    for a in selected:
        assumptions.extend(a.assumptions)
    findings = [
        _finding(
            result,
            _contributions(result, selected, catalog),
            catalogued=result.category in known_categories,
        )
        for result in results
    ]
    return findings, assumptions
