"""The generic catalog: ids, categories and spelling normalization. All data is generic."""

import pytest

from athlete_platform.domain.engine import (
    CATALOG,
    CATALOG_VERSION,
    METCON_ID,
    PULL_CATEGORIES,
    normalize_exercise_id,
    pull_categories,
    representative_exercise,
)
from athlete_platform.domain.engine.catalog import ID_PATTERN


def test_catalog_is_versioned_and_keys_match_exercise_ids() -> None:
    assert CATALOG_VERSION == "catalog-v1"
    assert CATALOG
    assert all(key == entry.id for key, entry in CATALOG.items())
    assert all(ID_PATTERN.fullmatch(key) for key in CATALOG)


def test_every_pull_category_has_exercises_and_only_known_categories_are_used() -> None:
    used = {entry.pull_category for entry in CATALOG.values() if entry.pull_category}
    assert used == set(PULL_CATEGORIES)
    assert pull_categories() == tuple(sorted(PULL_CATEGORIES))


@pytest.mark.parametrize(
    ("exercise", "category"),
    [
        ("pull_up", "strict_pull"),
        ("chin_up", "strict_pull"),
        ("chest_to_bar_pull_up", "strict_pull"),
        ("weighted_pull_up", "strict_pull"),
        ("rope_climb", "rope"),
        ("legless_rope_climb", "rope"),
        ("bar_muscle_up", "muscle_up"),
        ("ring_muscle_up", "muscle_up"),
        ("toes_to_bar", "kipping_core"),
        ("back_squat", None),
        ("bench_press", None),
        ("strict_press", None),
        ("burpee", None),
        ("clean_and_jerk", None),
        ("run", None),
        ("bike", None),
        ("row", None),
        ("ring_row", None),
    ],
)
def test_known_exercises_map_to_their_category(exercise: str, category: str | None) -> None:
    assert CATALOG[exercise].pull_category == category


def test_composite_and_unlisted_ids_are_absent() -> None:
    assert METCON_ID not in CATALOG
    assert "kipping_pull_up" not in CATALOG


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pull_up", "pull_up"),
        ("Pull-Up", "pull_up"),
        ("  pull up ", "pull_up"),
        ("back-squat", "back_squat"),
        ("TOES_TO_BAR", "toes_to_bar"),
        ("pullup", "pullup"),  # a different word is a different id
    ],
)
def test_normalize_exercise_id(raw: str, expected: str) -> None:
    assert normalize_exercise_id(raw) == expected


def test_representative_exercise_is_the_first_id_of_the_category() -> None:
    assert representative_exercise("strict_pull") == "chest_to_bar_pull_up"
    assert representative_exercise("rope") == "legless_rope_climb"
    assert representative_exercise("no_such_category") is None
    assert CATALOG[representative_exercise("muscle_up") or ""].pull_category == "muscle_up"
