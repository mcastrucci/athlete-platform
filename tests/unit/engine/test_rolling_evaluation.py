"""Weekly totals, rolling 7-day windows and the combined constraint evaluation.

Calendar used throughout (all invented): the evaluated week runs Monday 2030-03-04 (day 0) to
Sunday 2030-03-10 (day 6); day -2 is Saturday 2030-03-02, day -6 is Tuesday 2030-02-26 and day -7
is Monday 2030-02-25.
"""

import datetime
import math

import pytest

from athlete_platform.domain.engine import (
    Block,
    Component,
    PullCheckResult,
    PullConstraint,
    RollingPull,
    Session,
    evaluate_pull_constraints,
    rolling_pulls,
    weekly_pulls,
)
from tests.engine_fixtures import (
    CATALOG,
    WEEK_END,
    WEEK_START,
    day,
    logged,
    metcon,
    planned,
    session,
)

K1 = PullConstraint("K-1", {"vertical_pull": 35})


def evaluate(sessions: list[Session], *constraints: PullConstraint) -> list[PullCheckResult]:
    return evaluate_pull_constraints(
        sessions, CATALOG, constraints or [K1], week_start=WEEK_START, week_end=WEEK_END
    )


def by_key(results: list[PullCheckResult]) -> dict[tuple[str, str, str], PullCheckResult]:
    return {(r.kind, r.constraint_id, r.category): r for r in results}


def spike_sessions() -> list[Session]:
    """A Saturday log before the week plus planned Monday and Friday pull sessions."""
    saturday = session(
        -2,
        logged("pull_up", 6, 6, 6, 6),  # 4 x 6 = 24
        metcon(Component("pull_up", reps=10), rounds=2),  # 2 x 10 = 20
        performed=True,
    )
    return [saturday, session(0, planned("pull_up", 3, 5)), session(4, planned("pull_up", 3, 5))]


def test_rolling_window_catches_a_cross_week_spike_the_iso_week_misses() -> None:
    """Mirrors athlete@3f5897c test_plan.py::test_tope_de_traccion_en_ventana_movil."""
    results = by_key(evaluate(spike_sessions()))
    weekly = results[("weekly", "K-1", "vertical_pull")]
    rolling = results[("rolling", "K-1", "vertical_pull")]
    # Week alone: Monday 3 x 5 = 15 plus Friday 3 x 5 = 15 gives 30, within the cap of 35.
    assert (weekly.value, weekly.status) == (30, "ok")
    # Window Sat 03-02..Fri 03-08: Saturday 24 + 20 = 44, Monday 15, Friday 15 gives 74 (cap 35).
    assert (rolling.value, rolling.status) == (74, "fail")
    assert (rolling.window_start, rolling.window_end) == (day(-2), day(4))
    assert rolling.unknown_dates == ()


def test_rolling_pulls_reports_the_worst_window_and_its_end() -> None:
    """Mirrors athlete@3f5897c core.py::rolling_pulls."""
    result = rolling_pulls(
        spike_sessions(), CATALOG, [K1], week_start=WEEK_START, week_end=WEEK_END
    )
    # Window ending Monday 03-04: 44 + 15 = 59; ending Friday 03-08: 44 + 15 + 15 = 74 is the max.
    assert result == {"vertical_pull": RollingPull(74, day(4), ())}


def test_rolling_window_ties_resolve_to_the_earliest_end() -> None:
    """Mirrors athlete@3f5897c core.py::rolling_pulls (strict > keeps the first best window)."""
    result = rolling_pulls(
        [session(0, planned("pull_up", 3, 5))],
        CATALOG,
        [K1],
        week_start=WEEK_START,
        week_end=WEEK_END,
    )
    # Every window ending Monday through Sunday holds the same 3 x 5 = 15; Monday is first.
    assert result["vertical_pull"] == RollingPull(15, day(0), ())


def test_window_boundary_includes_six_days_before_and_excludes_seven() -> None:
    """Mirrors athlete@3f5897c core.py::rolling_pulls (window = end-6 .. end)."""
    monday = session(0, planned("pull_up", 3, 5))  # 15
    day_minus_6 = session(-6, logged("pull_up", 10), performed=True)
    day_minus_7 = session(-7, logged("pull_up", 100), performed=True)
    inside = rolling_pulls(
        [day_minus_6, monday], CATALOG, [K1], week_start=WEEK_START, week_end=WEEK_END
    )
    # The window ending Monday 03-04 spans Tue 02-26..Mon 03-04: 10 + 15 = 25.
    assert inside["vertical_pull"].value == 25
    outside = rolling_pulls(
        [day_minus_7, monday], CATALOG, [K1], week_start=WEEK_START, week_end=WEEK_END
    )
    # Mon 02-25 is seven days before Mon 03-04 and falls outside every window ending in the week.
    assert outside["vertical_pull"].value == 15


def test_planned_sessions_before_the_week_never_happened_and_are_ignored() -> None:
    """Engine contract (athlete@3f5897c core.py::rolling_pulls used only logs before the week)."""
    result = rolling_pulls(
        [session(-2, planned("pull_up", 3, 5))],
        CATALOG,
        [K1],
        week_start=WEEK_START,
        week_end=WEEK_END,
    )
    assert result["vertical_pull"].value == 0


def test_performed_sessions_inside_the_week_count_as_logged() -> None:
    """Engine contract: a partly performed week mixes logged and planned sessions."""
    sessions = [
        session(0, logged("pull_up", 8, 8), performed=True),
        session(2, planned("pull_up", 3, 5)),
    ]
    totals = weekly_pulls(sessions, CATALOG, week_start=WEEK_START, week_end=WEEK_END)
    assert totals.known == {"vertical_pull": 31}  # logged 8 + 8 = 16, planned 3 x 5 = 15


def test_no_prior_records_adds_no_rolling_row() -> None:
    """Mirrors athlete@3f5897c
    test_plan.py::test_ventana_movil_sin_registros_previos_no_agrega_filas.
    """
    results = evaluate([session(0, planned("pull_up", 3, 5)), session(3, planned("pull_up", 3, 5))])
    assert [(r.kind, r.status, r.value) for r in results] == [("weekly", "ok", 30)]  # 15 + 15


def test_unknown_dose_before_the_week_is_manual_never_ok() -> None:
    """Mirrors athlete@3f5897c test_plan.py::test_ventana_movil_dosis_desconocida_queda_manual."""
    sessions = [
        session(-2, metcon(Component("pull_up", reps=15)), performed=True),  # no rounds, no total
        session(0, planned("pull_up", 3, 5)),
    ]
    results = by_key(evaluate(sessions))
    rolling = results[("rolling", "K-1", "vertical_pull")]
    assert rolling.status == "manual"
    assert rolling.value == 15  # only Monday's 3 x 5 is known; the Saturday dose is not zero
    assert rolling.unknown_dates == (day(-2),)
    assert "2030-03-02" in rolling.detail
    assert results[("weekly", "K-1", "vertical_pull")].status == "ok"  # unknown is outside the week


def test_known_total_over_the_cap_fails_even_with_unknown_doses() -> None:
    """Mirrors athlete@3f5897c core.py::check_constraints: value > cap is checked before unknown."""
    sessions = [
        session(-3, metcon(Component("pull_up", reps=15)), performed=True),  # unknown
        session(-2, logged("pull_up", 40), performed=True),
    ]
    rolling = by_key(evaluate(sessions))[("rolling", "K-1", "vertical_pull")]
    assert (rolling.status, rolling.value, rolling.unknown_dates) == ("fail", 40, (day(-3),))


def test_confirmed_zero_is_not_unknown() -> None:
    """Mirrors athlete@3f5897c
    test_plan.py::test_ventana_movil_cero_confirmado_no_es_desconocido.
    """
    sessions = [
        session(-2, metcon(Component("pull_up", reps=15, completed_reps=0)), performed=True),
        session(0, planned("pull_up", 3, 5)),
    ]
    results = evaluate(sessions)
    assert [r.kind for r in results] == ["weekly"]  # prior days add nothing, nothing unknown
    assert results[0].status == "ok"


def test_negative_quantity_does_not_subtract_work() -> None:
    """Mirrors athlete@3f5897c test_plan.py::test_cantidad_negativa_no_resta_y_se_rechaza."""
    sessions = [
        session(
            -2,
            logged("pull_up", 20, 20),  # 40
            metcon(Component("pull_up", reps=10, completed_reps=-40), rounds=4),  # invalid: unknown
            performed=True,
        ),
        session(0, planned("pull_up", 3, 8)),  # 24
    ]
    rolling = by_key(evaluate(sessions))[("rolling", "K-1", "vertical_pull")]
    # 40 logged + 24 planned = 64 (the -40 is not subtracted); over the cap of 35 so it fails.
    assert (rolling.status, rolling.value) == ("fail", 64)
    assert rolling.unknown_dates == (day(-2),)


@pytest.mark.parametrize("bad", [True, math.nan, math.inf, -1])
def test_invalid_logged_set_reps_never_certify_the_cap(bad: float) -> None:
    """Mirrors athlete@3f5897c test_plan.py::test_cantidad_negativa_no_resta_y_se_rechaza."""
    sessions = [session(-2, logged("pull_up", 5, bad), performed=True)]
    rolling = by_key(evaluate(sessions))[("rolling", "K-1", "vertical_pull")]
    assert (rolling.status, rolling.value) == ("manual", 0)


def test_constraints_sharing_a_category_get_separate_results() -> None:
    """Mirrors athlete@3f5897c test_plan.py::test_ventana_movil_por_restriccion_con_revision_parcial
    (one row per constraint id; the manual-review override is not ported).
    """
    loose = PullConstraint("K-A", {"vertical_pull": 60, "rope": 10})
    tight = PullConstraint("K-B", {"vertical_pull": 40})
    sessions = [
        session(-2, logged("chin_up", 10, 10, 10), performed=True),  # 30
        session(0, planned("pull_up", 3, 5)),  # 15
        session(4, planned("pull_up", 3, 5)),  # 15
    ]
    results = by_key(evaluate(sessions, loose, tight))
    # Week: 15 + 15 = 30. Window Sat 03-02..Fri 03-08: 30 + 15 + 15 = 60.
    assert {k: (r.value, r.cap, r.status) for k, r in results.items()} == {
        ("weekly", "K-A", "vertical_pull"): (30, 60, "ok"),
        ("weekly", "K-A", "rope"): (0, 10, "ok"),
        ("weekly", "K-B", "vertical_pull"): (30, 40, "ok"),
        ("rolling", "K-A", "vertical_pull"): (60, 60, "ok"),  # the cap itself is allowed
        ("rolling", "K-B", "vertical_pull"): (60, 40, "fail"),
    }


def test_each_constraint_reports_the_unknown_dose_under_its_own_id() -> None:
    """Mirrors athlete@3f5897c
    test_plan.py::test_ventana_movil_por_restriccion_con_revision_parcial.
    """
    pulls = PullConstraint("K-A", {"vertical_pull": 35})
    core = PullConstraint("K-B", {"hanging_core": 35})
    sessions = [
        session(
            -2, metcon(Component("toes_to_bar", reps=15)), performed=True
        ),  # unknown, no rounds
    ]
    rows = {
        (r.kind, r.constraint_id, r.category): r.status for r in evaluate(sessions, pulls, core)
    }
    assert rows == {
        ("weekly", "K-A", "vertical_pull"): "ok",
        ("weekly", "K-B", "hanging_core"): "ok",
        ("rolling", "K-B", "hanging_core"): "manual",  # K-A's category is untouched
    }


def test_weekly_total_over_the_cap_fails() -> None:
    """Mirrors athlete@3f5897c core.py::check_constraints (weekly_pull_caps branch)."""
    results = evaluate([session(0, planned("pull_up", 3, 5)), session(2, planned("pull_up", 3, 8))])
    weekly = results[0]
    assert (weekly.kind, weekly.value, weekly.status) == ("weekly", 39, "fail")  # 15 + 24 > 35


def test_planned_open_format_caps_count_against_the_weekly_cap() -> None:
    """Mirrors athlete@3f5897c test_plan.py::test_tope_de_toes_to_bar_superado."""
    capped = PullConstraint("K-1", {"hanging_core": 60})
    block = metcon(Component("toes_to_bar", reps=5), caps={"toes_to_bar": 64})
    results = evaluate([session(2, block)], capped)
    assert [(r.kind, r.value, r.status) for r in results] == [("weekly", 64, "fail")]  # 64 > 60


def test_planned_unknown_dose_makes_the_week_manual() -> None:
    """Intentional difference from athlete@3f5897c core.py::compute_totals.

    The baseline added zero for a planned pull block without sets or reps and failed a separate
    plan check; the engine reports the week as manual.
    """
    results = evaluate([session(0, Block("pull_up", sets=3))])
    assert [(r.kind, r.status, r.unknown_dates) for r in results] == [
        ("weekly", "manual", (day(0),)),
        ("rolling", "manual", (day(0),)),
    ]


def test_conditional_component_is_not_planned_exposure_in_the_totals() -> None:
    """Mirrors athlete@3f5897c core.py::session_pulls (conditional components)."""
    block = metcon(Component("pull_up", reps=10, conditional=True), rounds=3)
    totals = weekly_pulls(
        [session(1, block), session(1, block, performed=True)],
        CATALOG,
        week_start=WEEK_START,
        week_end=WEEK_END,
    )
    assert totals.known == {} and totals.unknown_dates == {}


def test_inactive_constraints_are_not_evaluated() -> None:
    """Mirrors athlete@3f5897c core.py::pull_constraints (status must be active)."""
    paused = PullConstraint("K-9", {"vertical_pull": 1}, active=False)
    assert evaluate([session(0, planned("pull_up", 3, 5))], paused) == []


def test_week_end_before_week_start_is_rejected() -> None:
    """Engine contract: an inverted week is a caller error."""
    with pytest.raises(ValueError):
        evaluate_pull_constraints([], CATALOG, [K1], week_start=WEEK_END, week_end=WEEK_START)


def test_evaluation_is_pure_and_independent_of_the_wall_clock() -> None:
    """Engine contract: identical inputs give identical results; dates come only from inputs."""
    sessions = spike_sessions()
    assert evaluate(sessions) == evaluate(list(reversed(sessions)))
    far_future = datetime.date(2099, 1, 1)
    assert (
        evaluate_pull_constraints([], CATALOG, [K1], week_start=far_future, week_end=far_future)[
            0
        ].status
        == "ok"
    )
