"""Synthetic builders shared by the engine tests. All identifiers and dates are invented."""

import datetime

from athlete_platform.domain.engine import (
    Block,
    Catalog,
    Component,
    Exercise,
    Session,
    SetRecord,
)

# 2030-03-04 is a Monday; the evaluated ISO week runs Monday 2030-03-04 to Sunday 2030-03-10.
WEEK_START = datetime.date(2030, 3, 4)
WEEK_END = datetime.date(2030, 3, 10)


def day(offset: int) -> datetime.date:
    """Return the date ``offset`` days after the Monday that starts the evaluated week."""
    return WEEK_START + datetime.timedelta(days=offset)


CATALOG: Catalog = {
    entry.id: entry
    for entry in (
        Exercise("pull_up", "vertical_pull"),
        Exercise("chin_up", "vertical_pull"),
        Exercise("rope_climb", "rope"),
        Exercise("toes_to_bar", "hanging_core"),
        Exercise("row"),
        Exercise("back_squat"),
        Exercise("metcon"),
    )
}


def sets(*reps: float | None) -> tuple[SetRecord, ...]:
    return tuple(SetRecord(r) for r in reps)


def planned(exercise: str, n_sets: int, reps: float) -> Block:
    """A prescribed block: ``n_sets`` x ``reps``."""
    return Block(exercise, sets=n_sets, reps=reps)


def logged(exercise: str, *reps: float | None) -> Block:
    """A performed block with one record per set."""
    return Block(exercise, sets=sets(*reps))


def session(offset: int, *blocks: Block, performed: bool = False) -> Session:
    return Session(day(offset), tuple(blocks), performed=performed)


def metcon(
    *components: Component,
    rounds: float | None = None,
    caps: dict[str, float] | None = None,
) -> Block:
    return Block("metcon", components=components, rounds=rounds, caps=caps or {})
