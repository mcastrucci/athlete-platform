"""Dose helpers: counts, validity, ISO weeks, constraint filtering."""

import datetime
import math

import pytest

from athlete_platform.domain.engine import (
    Block,
    Component,
    PullConstraint,
    Session,
    component_ids,
    iso_week,
    iso_week_bounds,
    pull_constraints,
    quantity_errors,
    rep_count,
    set_count,
    valid_count,
)
from tests.engine_fixtures import day, logged, planned, session


def test_rep_count_sums_a_set_list_and_set_count_counts_records() -> None:
    """Mirrors athlete@3f5897c test_regressions.py::test_actual_sets_list_counts_reps_and_sets."""
    block = logged("pull_up", 4, 3)
    assert rep_count(block) == 7  # 4 + 3
    assert set_count(block) == 2  # two records


def test_rep_count_multiplies_integer_sets_by_reps() -> None:
    """Mirrors athlete@3f5897c core.py::rep_count (integer sets branch)."""
    block = planned("pull_up", 3, 5)
    assert rep_count(block) == 15  # 3 x 5
    assert set_count(block) == 3


def test_rep_count_is_unknown_when_the_dose_is_missing() -> None:
    """Mirrors athlete@3f5897c test_regressions.py::test_missing_reps_is_not_zero_volume.

    The baseline reported a missing dose as a failed plan check; the engine reports it as unknown.
    """
    assert rep_count(Block("pull_up", sets=3)) is None  # reps missing
    assert rep_count(Block("pull_up", reps=5)) is None  # sets missing
    assert rep_count(Block("pull_up")) is None
    assert set_count(Block("pull_up")) == 0


def test_rep_count_is_none_when_a_logged_set_has_no_reps() -> None:
    """Intentional difference from athlete@3f5897c core.py::rep_count.

    The baseline crashed on a set record whose reps were absent or null; the engine returns None.
    """
    block = logged("pull_up", 4, None, 3)
    assert rep_count(block) is None
    assert set_count(block) == 3  # the sets still happened


def test_rep_count_of_an_empty_set_list_is_zero() -> None:
    """Mirrors athlete@3f5897c core.py::rep_count: sum over no sets is 0."""
    assert rep_count(Block("pull_up", sets=())) == 0


@pytest.mark.parametrize("bad", [True, -1, math.nan, math.inf, -math.inf])
def test_invalid_reps_make_the_count_unknown(bad: float) -> None:
    """Mirrors athlete@3f5897c core.py::valid_count applied to rep_count inputs."""
    assert rep_count(logged("pull_up", 5, bad)) is None
    assert rep_count(Block("pull_up", sets=3, reps=bad)) is None


@pytest.mark.parametrize("bad", [True, -1, 2.5])
def test_set_count_rejects_invalid_integer_counts(bad: float) -> None:
    """Engine contract: set_count raises ValueError for boolean, negative or fractional counts."""
    with pytest.raises(ValueError):
        set_count(Block("pull_up", sets=bad))  # type: ignore[arg-type]


def test_valid_count_accepts_only_finite_non_negative_numbers() -> None:
    """Mirrors athlete@3f5897c core.py::valid_count."""
    assert valid_count(0)
    assert valid_count(7)
    assert valid_count(2.5)
    for bad in (True, False, -0.1, math.nan, math.inf, None, "5"):
        assert not valid_count(bad)


def test_component_ids_lists_component_exercises_in_order() -> None:
    """Mirrors athlete@3f5897c core.py::component_ids."""
    block = Block("metcon", components=(Component("pull_up"), Component("toes_to_bar")))
    assert component_ids(block) == ["pull_up", "toes_to_bar"]
    assert component_ids(Block("pull_up")) == []


def test_quantity_errors_name_each_invalid_quantity_and_ignore_unreported_ones() -> None:
    """Mirrors athlete@3f5897c core.py::validate_log_quantities (rejected at the boundary)."""
    block = Block(
        "metcon",
        components=(
            Component("pull_up", reps=10, completed_reps=-40),
            Component("toes_to_bar", reps=None, completed_reps=None),  # unreported: not an error
        ),
        rounds=math.nan,
        caps={"pull_up": True},
    )
    errors = quantity_errors(
        Session(day(-2), (block, logged("pull_up", 5, True, None)), performed=True)
    )
    assert len(errors) == 4
    assert any("pull_up.completed_reps" in e and "-40" in e for e in errors)
    assert any("rounds" in e for e in errors)
    assert any("caps.pull_up" in e for e in errors)
    assert any("sets[].reps" in e and "True" in e for e in errors)
    assert quantity_errors(session(0, planned("pull_up", 3, 5))) == []


def test_iso_week_label_and_bounds() -> None:
    """Mirrors athlete@3f5897c core.py::iso_week."""
    assert iso_week(datetime.date(2030, 3, 4)) == "2030-W10"  # 63 days after Monday 2029-12-31
    assert (
        iso_week(datetime.date(2029, 12, 31)) == "2030-W01"
    )  # ISO year differs from calendar year
    assert iso_week_bounds(datetime.date(2030, 3, 7)) == (
        datetime.date(2030, 3, 4),
        datetime.date(2030, 3, 10),
    )


def test_pull_constraints_keeps_active_constraints_with_caps() -> None:
    """Mirrors athlete@3f5897c core.py::pull_constraints (status active, caps present)."""
    active = PullConstraint("K-1", {"vertical_pull": 20})
    paused = PullConstraint("K-2", {"vertical_pull": 20}, active=False)
    empty = PullConstraint("K-3", {})
    assert pull_constraints([active, paused, empty]) == [active]


@pytest.mark.parametrize("bad", [-1, True, math.nan, math.inf])
def test_constraint_caps_must_be_finite_non_negative(bad: float) -> None:
    """Engine contract: invalid caps are configuration errors and raise ValueError."""
    with pytest.raises(ValueError):
        PullConstraint("K-1", {"vertical_pull": bad})
    with pytest.raises(ValueError):
        PullConstraint("", {"vertical_pull": 1})


def test_session_date_must_be_a_date_not_a_datetime() -> None:
    """Engine contract: no time-of-day or timezone enters the engine."""
    with pytest.raises(TypeError):
        Session(datetime.datetime(2030, 3, 4, 12, 0))
