"""Typed inputs of the pull-budget engine.

Plain frozen dataclasses with no framework imports. Quantities (reps, rounds, caps) are typed
``float | None`` but are validated at run time by :func:`valid_count`, because callers may hand over
values parsed from untrusted documents (booleans, NaN, negatives). ``None`` always means "not
reported", which the engine treats as an unknown dose and never as zero.
"""

import datetime
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TypeGuard

Quantity = float | None


def valid_count(value: object) -> TypeGuard[float]:
    """Return whether ``value`` is a finite, non-negative, non-boolean number."""
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


@dataclass(frozen=True)
class Exercise:
    """A catalog entry. ``pull_category`` is ``None`` for exercises outside any pull budget."""

    id: str
    pull_category: str | None = None


Catalog = Mapping[str, Exercise]


@dataclass(frozen=True)
class SetRecord:
    """One performed or planned set. ``reps=None`` means the repetitions were not reported."""

    reps: Quantity = None


@dataclass(frozen=True)
class Component:
    """A movement inside a composite block such as a metcon.

    ``reps`` is the prescribed repetitions per round, ``completed_reps`` the total actually done
    (``0`` is a confirmed zero, ``None`` is unreported), and ``conditional`` marks a replacement
    that only counts when the athlete reports repetitions for it.
    """

    exercise: str
    reps: Quantity = None
    completed_reps: Quantity = None
    conditional: bool = False


@dataclass(frozen=True)
class Block:
    """A block of a session.

    ``sets`` is a set count (prescription) or a list of set records (log). ``caps`` maps component
    exercise ids to the repetitions reserved for them in a planned open format. ``rounds`` is the
    number of complete rounds reported in a logged result.
    """

    exercise: str
    sets: int | tuple[SetRecord, ...] | None = None
    reps: Quantity = None
    caps: Mapping[str, float] = field(default_factory=dict)
    components: tuple[Component, ...] = ()
    rounds: Quantity = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "caps", MappingProxyType(dict(self.caps)))


@dataclass(frozen=True)
class Session:
    """A dated session. ``performed`` selects log semantics (True) or plan semantics (False)."""

    date: datetime.date
    blocks: tuple[Block, ...] = ()
    performed: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.date, datetime.datetime) or not isinstance(self.date, datetime.date):
            raise TypeError("Session.date must be a datetime.date, not a datetime")


@dataclass(frozen=True)
class PullConstraint:
    """A constraint capping pull repetitions per category over a week or any 7-day window."""

    id: str
    weekly_caps: Mapping[str, int | float]
    active: bool = True

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("PullConstraint.id must not be empty")
        for category, cap in self.weekly_caps.items():
            if not valid_count(cap):
                raise ValueError(
                    f"cap for {category!r} must be a finite non-negative number: {cap!r}"
                )
        object.__setattr__(self, "weekly_caps", MappingProxyType(dict(self.weekly_caps)))


@dataclass(frozen=True)
class PullCheckResult:
    """The outcome for one constraint and category.

    ``kind`` is ``"weekly"`` (the evaluated ISO week) or ``"rolling"`` (the worst 7-day window
    ending inside it). ``window_start``/``window_end`` bound the span the value covers.
    ``unknown_dates`` lists days whose pull dose could not be determined.
    """

    constraint_id: str
    category: str
    kind: str
    cap: float
    value: float
    window_start: datetime.date
    window_end: datetime.date
    status: str
    unknown_dates: tuple[datetime.date, ...] = ()
    detail: str = ""


Sessions = Sequence[Session]
