"""Generic, versioned exercise catalog for the pull-budget engine.

This is generic movement knowledge, not athlete data: it maps stable exercise ids to the pull
category each movement counts toward. Ids are lowercase snake_case. An id that is not listed here
has no known category; the evaluation never treats that as "zero pulls" (see
docs/engineering/evaluation.md), because an unlisted movement could be a pull.

Pull categories:

``strict_pull``
    Vertical pulling to the bar or rings with a controlled hang: pull-up, chin-up, chest-to-bar
    pull-up and the weighted pull-up.
``rope``
    Rope climbs, with or without the legs.
``muscle_up``
    Bar and ring muscle-ups (a pull and a transition).
``kipping_core``
    Hanging, swinging core work: toes-to-bar.

Judgment calls, open to a later catalog version:

- ``ring_row`` is a horizontal pull and is listed without a category, so it does not count toward
  ``strict_pull``.
- ``kipping_pull_up`` and other ids that are not listed are unknown, not guessed.
- ``metcon`` is not a movement and is deliberately absent: composite items carry no breakdown
  here, so they are unknown.

Any change to the mapping, including adding an id, must bump :data:`CATALOG_VERSION`: stored
evaluations record the version they used, and a new version makes them stale.
"""

import re
from types import MappingProxyType

from .model import Catalog, Exercise

CATALOG_VERSION = "catalog-v1"

STRICT_PULL = "strict_pull"
ROPE = "rope"
MUSCLE_UP = "muscle_up"
KIPPING_CORE = "kipping_core"

PULL_CATEGORIES: tuple[str, ...] = (STRICT_PULL, ROPE, MUSCLE_UP, KIPPING_CORE)

_PULLS: dict[str, tuple[str, ...]] = {
    STRICT_PULL: ("pull_up", "chin_up", "chest_to_bar_pull_up", "weighted_pull_up"),
    ROPE: ("rope_climb", "legless_rope_climb"),
    MUSCLE_UP: ("bar_muscle_up", "ring_muscle_up"),
    KIPPING_CORE: ("toes_to_bar",),
}

_NON_PULLS: tuple[str, ...] = (
    "back_squat",
    "front_squat",
    "overhead_squat",
    "deadlift",
    "romanian_deadlift",
    "bench_press",
    "strict_press",
    "push_press",
    "push_jerk",
    "snatch",
    "power_snatch",
    "clean",
    "power_clean",
    "clean_and_jerk",
    "thruster",
    "burpee",
    "box_jump",
    "wall_ball",
    "kettlebell_swing",
    "walking_lunge",
    "push_up",
    "handstand_push_up",
    "dip",
    "ring_row",
    "sit_up",
    "plank",
    "run",
    "bike",
    "row",
    "ski_erg",
    "swim",
    "jump_rope",
    "double_under",
)

ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")

_ENTRIES = tuple(
    [
        Exercise(id=exercise, pull_category=category)
        for category, ids in _PULLS.items()
        for exercise in ids
    ]
    + [Exercise(id=exercise) for exercise in _NON_PULLS]
)

CATALOG: Catalog = MappingProxyType({entry.id: entry for entry in _ENTRIES})


def normalize_exercise_id(raw: str) -> str:
    """Return the catalog spelling of ``raw``: trimmed, lowercase, hyphens and spaces as ``_``.

    ``"Pull-Up"`` and ``"pull up"`` both become ``"pull_up"``. Nothing else is guessed: a
    different word is a different id.
    """
    return re.sub(r"[\s\-]+", "_", raw.strip().lower())


def representative_exercise(category: str, catalog: Catalog = CATALOG) -> str | None:
    """Return the alphabetically first catalog id of ``category``, or ``None`` if there is none."""
    ids = sorted(e.id for e in catalog.values() if e.pull_category == category)
    return ids[0] if ids else None


def pull_categories(catalog: Catalog = CATALOG) -> tuple[str, ...]:
    """Return the sorted pull categories that at least one exercise of ``catalog`` has."""
    return tuple(sorted({e.pull_category for e in catalog.values() if e.pull_category}))
