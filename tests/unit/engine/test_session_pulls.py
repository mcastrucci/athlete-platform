"""Per-session pull dose, planned and logged."""

import math

import pytest

from athlete_platform.domain.engine import METCON_ID, Block, Component, Exercise, session_pulls
from tests.engine_fixtures import CATALOG, logged, metcon, planned, session


def test_planned_pull_block_counts_sets_times_reps() -> None:
    """Mirrors athlete@3f5897c core.py::session_pulls (plan branch) and
    tools/tests/test_regressions.py::test_pull_reps_change_recalculates_budget.
    """
    known, unknown = session_pulls(
        session(0, planned("pull_up", 3, 5), planned("row", 4, 8)), CATALOG, logged=False
    )
    assert known == {"vertical_pull": 15}  # 3 x 5; row has no pull category
    assert unknown == set()


def test_planned_open_format_counts_reserved_caps() -> None:
    """Mirrors athlete@3f5897c core.py::session_pulls (caps) and
    tools/tests/test_plan.py::test_tope_de_toes_to_bar_superado (reserved caps are the budget).
    """
    block = metcon(
        Component("pull_up", reps=10),
        Component("toes_to_bar", reps=5),
        caps={"pull_up": 30, "toes_to_bar": 20, "back_squat": 99},
    )
    known, unknown = session_pulls(
        session(0, block, planned("pull_up", 3, 5)), CATALOG, logged=False
    )
    # 3 x 5 = 15 plus the 30 reserved for pull_up = 45; 20 for toes_to_bar.
    # back_squat has no pull category, so its cap is ignored.
    assert known == {"vertical_pull": 45, "hanging_core": 20}
    assert unknown == set()


def test_planned_open_format_ignores_component_reps_without_caps() -> None:
    """Mirrors athlete@3f5897c core.py::session_pulls: only caps count for planned metcons."""
    block = metcon(Component("pull_up", reps=10), rounds=3)
    assert session_pulls(session(0, block), CATALOG, logged=False) == ({}, set())


def test_planned_conditional_component_is_not_planned_exposure() -> None:
    """Mirrors athlete@3f5897c core.py::session_pulls: conditional replacements add nothing."""
    block = metcon(Component("pull_up", reps=10, conditional=True))
    assert session_pulls(session(0, block), CATALOG, logged=False) == ({}, set())


def test_planned_missing_dose_is_unknown_not_zero() -> None:
    """Intentional difference from athlete@3f5897c core.py::session_pulls (plan branch).

    The baseline added `rep_count(b) or 0` and relied on a separate plan check; the engine marks
    the category unknown.
    """
    known, unknown = session_pulls(session(0, Block("pull_up", sets=3)), CATALOG, logged=False)
    assert known == {}
    assert unknown == {"vertical_pull"}


def test_planned_invalid_cap_is_unknown() -> None:
    """Engine contract: an invalid reserved cap never counts as zero."""
    block = metcon(Component("pull_up"), caps={"pull_up": -5})
    assert session_pulls(session(0, block), CATALOG, logged=False) == ({}, {"vertical_pull"})


def test_logged_pull_block_counts_performed_sets() -> None:
    """Mirrors athlete@3f5897c core.py::session_pulls (log branch)."""
    known, unknown = session_pulls(
        session(0, logged("pull_up", 6, 6, 6, 6), performed=True), CATALOG, logged=True
    )
    assert known == {"vertical_pull": 24}  # 4 x 6
    assert unknown == set()


def test_logged_block_without_a_usable_set_list_is_unknown() -> None:
    """Mirrors athlete@3f5897c core.py::session_pulls: no data is not zero in the log."""
    for block in (
        Block("pull_up"),  # nothing recorded
        Block("pull_up", sets=3, reps=5),  # a prescription, not a log
        Block("pull_up", sets=()),  # empty list
        logged("pull_up", 5, None),  # one set without reps
    ):
        assert session_pulls(session(0, block), CATALOG, logged=True) == ({}, {"vertical_pull"})


@pytest.mark.parametrize("bad", [-40, True, math.nan, math.inf])
def test_logged_invalid_set_reps_are_unknown_and_subtract_nothing(bad: float) -> None:
    """Mirrors athlete@3f5897c test_plan.py::test_cantidad_negativa_no_resta_y_se_rechaza."""
    block = logged("pull_up", 20, bad)
    assert session_pulls(session(0, block), CATALOG, logged=True) == ({}, {"vertical_pull"})


def test_logged_metcon_prefers_completed_reps_over_rounds_times_reps() -> None:
    """Mirrors athlete@3f5897c core.py::session_pulls (metcon components)."""
    block = metcon(
        Component("pull_up", reps=10, completed_reps=14),
        Component("toes_to_bar", reps=5),
        rounds=3,
    )
    known, unknown = session_pulls(session(0, block), CATALOG, logged=True)
    # pull_up: completed_reps 14 wins over 3 rounds x 10 = 30; toes_to_bar: 3 rounds x 5 = 15.
    assert known == {"vertical_pull": 14, "hanging_core": 15}
    assert unknown == set()


def test_logged_metcon_counts_rounds_times_reps_per_round() -> None:
    """Mirrors athlete@3f5897c test_plan.py::test_tope_de_traccion_en_ventana_movil
    (metcon with 20 pull-ups recorded as rounds x reps).
    """
    block = metcon(Component("pull_up", reps=10), rounds=2)
    assert session_pulls(session(0, block), CATALOG, logged=True) == (
        {"vertical_pull": 20},
        set(),
    )  # 2 x 10


def test_logged_metcon_without_rounds_or_completed_reps_is_unknown() -> None:
    """Mirrors athlete@3f5897c test_plan.py::test_ventana_movil_dosis_desconocida_queda_manual."""
    block = metcon(Component("pull_up", reps=15))  # time-capped result, no rounds
    assert session_pulls(session(0, block), CATALOG, logged=True) == ({}, {"vertical_pull"})


def test_logged_confirmed_zero_is_known_not_unknown() -> None:
    """Mirrors athlete@3f5897c
    test_plan.py::test_ventana_movil_cero_confirmado_no_es_desconocido.
    """
    block = metcon(Component("pull_up", reps=15, completed_reps=0))
    assert session_pulls(session(0, block), CATALOG, logged=True) == ({"vertical_pull": 0}, set())


@pytest.mark.parametrize("bad", [-40, True, math.nan, math.inf])
def test_logged_invalid_completed_reps_are_unknown(bad: float) -> None:
    """Mirrors athlete@3f5897c test_plan.py::test_cantidad_negativa_no_resta_y_se_rechaza."""
    block = metcon(Component("pull_up", reps=10, completed_reps=bad), rounds=4)
    assert session_pulls(session(0, block), CATALOG, logged=True) == ({}, {"vertical_pull"})


def test_logged_invalid_rounds_do_not_count() -> None:
    """Mirrors athlete@3f5897c core.py::session_pulls: rounds must pass valid_count."""
    block = metcon(Component("pull_up", reps=10), rounds=-2)
    assert session_pulls(session(0, block), CATALOG, logged=True) == ({}, {"vertical_pull"})


def test_logged_conditional_component_counts_only_with_reported_reps() -> None:
    """Mirrors athlete@3f5897c core.py::session_pulls: an unreported conditional was not done."""
    skipped = metcon(Component("pull_up", reps=10, conditional=True), rounds=3)
    assert session_pulls(session(0, skipped), CATALOG, logged=True) == ({}, set())
    done = metcon(Component("pull_up", reps=10, conditional=True, completed_reps=6), rounds=3)
    assert session_pulls(session(0, done), CATALOG, logged=True) == ({"vertical_pull": 6}, set())


def test_logged_mode_ignores_reserved_caps() -> None:
    """Mirrors athlete@3f5897c core.py::session_pulls: caps are a plan-only reservation."""
    block = metcon(Component("pull_up", reps=10, completed_reps=12), rounds=2, caps={"pull_up": 40})
    assert session_pulls(session(0, block), CATALOG, logged=True) == ({"vertical_pull": 12}, set())


def test_the_metcon_block_itself_never_contributes_through_sets() -> None:
    """Mirrors athlete@3f5897c core.py::session_pulls: `exercise != "metcon"` guard."""
    catalog = {**CATALOG, METCON_ID: Exercise(METCON_ID, "vertical_pull")}
    block = Block(METCON_ID, sets=5, reps=10)
    assert session_pulls(session(0, block), catalog, logged=False) == ({}, set())


def test_exercises_missing_from_the_catalog_are_ignored() -> None:
    """Mirrors athlete@3f5897c core.py::session_pulls: unknown exercise has no pull category."""
    assert session_pulls(session(0, planned("handstand_walk", 3, 5)), CATALOG, logged=False) == (
        {},
        set(),
    )
