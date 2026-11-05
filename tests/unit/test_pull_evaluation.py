"""The canonical-session adapter and the pure week evaluation.

Calendar (all invented): the evaluated week is 2030-W10, Monday 2030-03-04 to Sunday 2030-03-10.
Offsets count days from that Monday, so offset -2 is Saturday 2030-03-02 (inside the 7-day span
before the week) and offset -7 is outside it.
"""

import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from athlete_platform.application.profiles import PullBudget
from athlete_platform.application.pull_evaluation import adapt_session, evaluate_week
from athlete_platform.application.sessions import CanonicalItem, CanonicalSession
from athlete_platform.domain.engine import (
    CATALOG,
    METCON_ID,
    Block,
    Component,
    PullCheckResult,  # noqa: F401  (documentation of parity)
    PullConstraint,
    Session,
    SetRecord,
    evaluate_pull_constraints,
    session_pulls,
    weekly_pulls,
)

WEEK = "2030-W10"
MONDAY = datetime.date(2030, 3, 4)
SUNDAY = datetime.date(2030, 3, 10)
ALL_CATEGORIES: dict[str, float] = {
    "strict_pull": 100,
    "rope": 100,
    "muscle_up": 100,
    "kipping_core": 100,
}


def day(offset: int) -> datetime.date:
    return MONDAY + datetime.timedelta(days=offset)


def item(position: int, exercise_id: str | None, **fields: Any) -> CanonicalItem:
    return CanonicalItem(
        position=position,
        exercise_text=fields.pop("text", "synthetic item"),
        exercise_id=exercise_id,
        **fields,
    )


def canonical(
    offset: int,
    *items: CanonicalItem,
    status: Literal["planned", "performed"] = "performed",
    session_id: UUID | None = None,
) -> CanonicalSession:
    return CanonicalSession(
        id=session_id or uuid4(),
        athlete_id=UUID(int=1),
        source_id=uuid4(),
        position=0,
        status=status,
        session_date=day(offset),
        items=list(items),
        ambiguities=[],
    )


def pulls(adapted_session: CanonicalSession, category: str = "strict_pull") -> tuple[float, bool]:
    adapted = adapt_session(adapted_session)
    logged = adapted.session.performed
    known, unknown = session_pulls(adapted.session, CATALOG, logged=logged)
    return known.get(category, 0), category in unknown


def one(
    *, status: Literal["planned", "performed"] = "performed", **fields: Any
) -> CanonicalSession:
    exercise = fields.pop("exercise", "pull_up")
    return canonical(0, item(0, exercise, **fields), status=status)


# ------------------------------------------------------------------ dose mapping


def test_performed_sets_and_reps_become_one_record_per_set() -> None:
    adapted = adapt_session(one(sets=3, reps=5))
    assert adapted.session.performed is True
    assert adapted.session.blocks[0].sets == (SetRecord(5),) * 3
    assert pulls(one(sets=3, reps=5)) == (15, False)


def test_performed_missing_reps_or_sets_stay_unknown_never_zero() -> None:
    assert pulls(one(sets=3)) == (0, True)
    assert pulls(one(reps=10)) == (0, True)  # reps without a set count are not guessed as one set
    assert pulls(one()) == (0, True)
    assert pulls(one(duration={"value": 90, "unit": "s"})) == (0, True)


def test_explicit_zero_stays_zero_and_is_not_unknown() -> None:
    assert pulls(one(sets=3, reps=0)) == (0, False)
    assert pulls(one(sets=0, reps=5)) == (0, False)
    assert pulls(one(sets=0)) == (0, False)
    assert pulls(one(reps=0)) == (0, False)
    assert pulls(one(rounds=0)) == (0, False)
    assert pulls(one(sets=0, reps=5, status="planned")) == (0, False)
    assert pulls(one(sets=0, status="planned")) == (0, False)
    assert pulls(one(rounds=0, status="planned")) == (0, False)


def test_planned_sessions_use_plan_semantics() -> None:
    planned = one(status="planned", sets=4, reps=6)
    adapted = adapt_session(planned)
    assert adapted.session.performed is False
    assert adapted.session.blocks[0] == Block("pull_up", sets=4, reps=6)
    assert pulls(planned) == (24, False)
    assert pulls(one(status="planned", sets=4)) == (0, True)
    assert pulls(one(status="planned", reps=6)) == (0, True)


def test_rounds_and_reps_follow_component_semantics() -> None:
    performed = adapt_session(one(exercise="toes_to_bar", rounds=5, reps=10))
    block = performed.session.blocks[0]
    assert block.exercise == METCON_ID
    assert block.rounds == 5
    assert block.components == (Component("toes_to_bar", reps=10, completed_reps=50),)
    assert pulls(one(exercise="toes_to_bar", rounds=5, reps=10), "kipping_core") == (50, False)
    assert pulls(one(exercise="toes_to_bar", rounds=5), "kipping_core") == (0, True)
    assert pulls(one(exercise="toes_to_bar", reps=10, rounds=None), "kipping_core") == (0, True)


def test_planned_rounds_reserve_rounds_times_reps_and_unknown_without_reps() -> None:
    assert pulls(
        one(exercise="toes_to_bar", rounds=4, reps=5, status="planned"), "kipping_core"
    ) == (
        20,
        False,
    )
    assert pulls(one(exercise="toes_to_bar", rounds=4, status="planned"), "kipping_core") == (
        0,
        True,
    )


def test_sets_together_with_rounds_is_ambiguous_and_unknown() -> None:
    ambiguous = one(sets=3, rounds=2, reps=5)
    assert pulls(ambiguous) == (0, True)
    adapted = adapt_session(ambiguous)
    assert [a.code for a in adapted.assumptions] == ["ambiguous_dose"]
    non_pull = adapt_session(one(exercise="back_squat", sets=3, rounds=2, reps=5))
    assert non_pull.assumptions == ()


def test_load_duration_and_distance_are_never_read() -> None:
    plain = pulls(one(sets=3, reps=5))
    loaded = pulls(
        one(
            sets=3,
            reps=5,
            load={"value": 20, "unit": "kg"},
            duration={"value": 4, "unit": "min"},
            distance={"value": 1, "unit": "km"},
        )
    )
    assert plain == loaded == (15, False)


def test_non_pull_exercises_never_matter_even_without_a_dose() -> None:
    adapted = adapt_session(one(exercise="back_squat"))
    assert adapted.assumptions == ()
    known, unknown = session_pulls(adapted.session, CATALOG, logged=True)
    assert known == {} and unknown == set()


def test_exercise_ids_are_normalized_before_lookup() -> None:
    assert pulls(one(exercise="Pull-Up", sets=2, reps=5)) == (10, False)


# ------------------------------------------------------------------ unclassified items


def test_item_without_an_exercise_id_is_unknown_for_every_category_and_reported() -> None:
    session = canonical(0, item(0, None, text="mystery movement", sets=3, reps=5))
    adapted = adapt_session(session)
    codes = [(a.code, a.session_id, a.item_position) for a in adapted.assumptions]
    assert codes == [("unclassified_item", session.id, 0)]
    known, unknown = session_pulls(adapted.session, CATALOG, logged=True)
    assert known == {}
    assert unknown == set(ALL_CATEGORIES)


def test_id_missing_from_the_catalog_is_reported_and_unknown_for_every_category() -> None:
    session = canonical(0, item(0, "back_squat", sets=3, reps=5), item(1, "banded_pullover"))
    adapted = adapt_session(session)
    assert [(a.code, a.item_position) for a in adapted.assumptions] == [("unknown_exercise_id", 1)]
    assert "banded_pullover" in adapted.assumptions[0].detail
    _, unknown = session_pulls(adapted.session, CATALOG, logged=True)
    assert unknown == set(ALL_CATEGORIES)


def test_unclassified_item_makes_the_week_manual_never_ok() -> None:
    budgets = [PullBudget(id="b", weekly_caps=ALL_CATEGORIES)]
    sessions = [
        canonical(1, item(0, "pull_up", sets=3, reps=5)),
        canonical(2, item(0, None, text="mystery movement", sets=3, reps=5)),
    ]
    findings, assumptions = evaluate_week(sessions, budgets, week=WEEK)
    weekly = {f.category: f for f in findings if f.kind == "weekly"}
    assert {f.status for f in weekly.values()} == {"manual"}
    assert weekly["strict_pull"].value == 15
    assert all(f.unknown_dates == [day(2)] for f in weekly.values())
    assert [a.code for a in assumptions] == ["unclassified_item"]
    assert assumptions[0].session_id == sessions[1].id and assumptions[0].item_position == 0


def test_a_log_replaces_a_plan_with_an_unclassified_item() -> None:
    budgets = [PullBudget(id="b", weekly_caps={"strict_pull": 100})]
    plan = canonical(1, item(0, None), status="planned")
    log = canonical(1, item(0, "pull_up", sets=2, reps=5))
    findings, assumptions = evaluate_week([plan, log], budgets, week=WEEK)
    assert [(f.status, f.value) for f in findings] == [("ok", 10)]
    assert assumptions == []  # the replaced plan never counted, so its item is not an assumption


# ------------------------------------------------------------------ parity with the engine


def hand_built_week() -> list[Session]:
    return [
        Session(
            day(-2), (Block("pull_up", sets=(SetRecord(8), SetRecord(8), SetRecord(8))),), True
        ),
        Session(day(-3), (Block("pull_up", sets=3, reps=20),), False),  # past plan: ignored
        Session(day(0), (Block("pull_up", sets=(SetRecord(10),) * 2),), True),
        Session(day(0), (Block("pull_up", sets=5, reps=10),), False),  # replaced by the log
        Session(day(1), (Block("rope_climb", sets=4, reps=3), Block("back_squat", sets=5, reps=5))),
        Session(
            day(2),
            (
                Block(
                    METCON_ID,
                    rounds=4,
                    components=(Component("toes_to_bar", reps=6, completed_reps=24),),
                ),
                Block("bar_muscle_up", sets=(SetRecord(2), SetRecord(1), SetRecord(0))),
            ),
            True,
        ),
        Session(day(4), (Block("chin_up", sets=3, reps=None),), False),
    ]


def canonical_week() -> list[CanonicalSession]:
    return [
        canonical(-2, item(0, "pull_up", sets=3, reps=8)),
        canonical(-3, item(0, "pull_up", sets=3, reps=20), status="planned"),
        canonical(0, item(0, "pull_up", sets=2, reps=10)),
        canonical(0, item(0, "pull_up", sets=5, reps=10), status="planned"),
        canonical(
            1,
            item(0, "rope_climb", sets=4, reps=3),
            item(1, "back_squat", sets=5, reps=5, load={"value": 100, "unit": "kg"}),
            status="planned",
        ),
        canonical(
            2,
            item(0, "toes_to_bar", rounds=4, reps=6),
            item(1, "bar_muscle_up", sets=3, reps=1),
        ),
        canonical(4, item(0, "chin_up", sets=3), status="planned"),
    ]


def test_adapter_totals_match_hand_built_engine_inputs() -> None:
    # The canonical bar muscle-up item is 3 sets of 1; the hand-built one is 2+1+0 = 3 reps too.
    hand = hand_built_week()
    adapted = [adapt_session(s).session for s in canonical_week()]
    expected = weekly_pulls(hand, CATALOG, week_start=MONDAY, week_end=SUNDAY)
    actual = weekly_pulls(adapted, CATALOG, week_start=MONDAY, week_end=SUNDAY)
    assert dict(actual.known) == dict(expected.known)
    assert dict(actual.unknown_dates) == dict(expected.unknown_dates)
    assert dict(actual.known) == {
        "strict_pull": 20,
        "rope": 12,
        "kipping_core": 24,
        "muscle_up": 3,
    }
    assert dict(actual.unknown_dates) == {"strict_pull": (day(4),)}


def test_evaluation_findings_match_the_engine_results_field_by_field() -> None:
    budgets = [
        PullBudget(id="tight", weekly_caps={"strict_pull": 40, "rope": 10}),
        PullBudget(id="loose", weekly_caps={"kipping_core": 100}),
        PullBudget(id="off", weekly_caps={"muscle_up": 0}, active=False),
    ]
    findings, assumptions = evaluate_week(canonical_week(), budgets, week=WEEK)
    constraints = [PullConstraint(b.id, b.weekly_caps, b.active) for b in budgets]
    engine = evaluate_pull_constraints(
        hand_built_week(), CATALOG, constraints, week_start=MONDAY, week_end=SUNDAY
    )
    assert assumptions == []
    assert len(findings) == len(engine) > 0
    for finding, result in zip(findings, engine, strict=True):
        assert (
            finding.constraint_id,
            finding.category,
            finding.kind,
            finding.cap,
            finding.value,
            finding.window_start,
            finding.window_end,
            finding.status,
            tuple(finding.unknown_dates),
        ) == (
            result.constraint_id,
            result.category,
            result.kind,
            result.cap,
            result.value,
            result.window_start,
            result.window_end,
            result.status,
            result.unknown_dates,
        )
        assert finding.catalog_version == "catalog-v1"
        assert finding.engine_version == "pull-budget-v1"


# ------------------------------------------------------------------ findings, contributions


def test_weekly_and_rolling_findings_list_the_contributing_items() -> None:
    first = canonical(-2, item(0, "pull_up", sets=3, reps=8))  # 24 before the week
    second = canonical(
        1, item(0, "back_squat", sets=3, reps=5), item(1, "pull_up", sets=1, reps=12)
    )
    budgets = [PullBudget(id="b", weekly_caps={"strict_pull": 30})]
    findings, _ = evaluate_week([first, second], budgets, week=WEEK)
    weekly = next(f for f in findings if f.kind == "weekly")
    rolling = next(f for f in findings if f.kind == "rolling")
    assert (weekly.value, weekly.status) == (12, "ok")
    assert [(c.session_id, c.item_position, c.pulls) for c in weekly.contributions] == [
        (second.id, 1, 12)
    ]
    assert (rolling.value, rolling.status) == (36, "fail")
    assert rolling.window_end == day(1)
    assert [(c.session_id, c.item_position, c.pulls) for c in rolling.contributions] == [
        (first.id, 0, 24),
        (second.id, 1, 12),
    ]


def test_unknown_items_are_listed_with_null_pulls() -> None:
    session = canonical(1, item(0, "pull_up", sets=3), item(1, "pull_up", sets=2, reps=5))
    budgets = [PullBudget(id="b", weekly_caps={"strict_pull": 30})]
    (finding, *_), _ = evaluate_week([session], budgets, week=WEEK)
    assert finding.status == "manual"
    assert finding.unknown_dates == [day(1)]
    assert [(c.item_position, c.pulls) for c in finding.contributions] == [(0, None), (1, 10)]


def test_sessions_outside_the_span_do_not_count() -> None:
    outside = [
        canonical(-7, item(0, "pull_up", sets=9, reps=9)),
        canonical(7, item(0, "pull_up", sets=9, reps=9)),
    ]
    budgets = [PullBudget(id="b", weekly_caps={"strict_pull": 1})]
    findings, _ = evaluate_week(outside, budgets, week=WEEK)
    assert [(f.kind, f.value, f.status) for f in findings] == [("weekly", 0, "ok")]


def test_no_budgets_still_yields_an_evaluation_with_an_assumption() -> None:
    no_budgets: list[list[PullBudget] | None] = [None, []]
    for budgets in no_budgets:
        findings, assumptions = evaluate_week([canonical(0, item(0, None))], budgets, week=WEEK)
        assert findings == []
        assert [a.code for a in assumptions] == ["no_budgets_configured"]


def test_only_inactive_budgets_are_reported_as_such() -> None:
    budgets = [PullBudget(id="b", weekly_caps={"strict_pull": 1}, active=False)]
    findings, assumptions = evaluate_week([], budgets, week=WEEK)
    assert findings == []
    assert [a.code for a in assumptions] == ["no_active_budgets"]


def test_a_cap_on_an_uncatalogued_category_cannot_pass() -> None:
    budgets = [PullBudget(id="typo", weekly_caps={"strict_pul": 5, "rope": 5})]
    findings, assumptions = evaluate_week([], budgets, week=WEEK)
    by_category = {f.category: f for f in findings}
    assert by_category["strict_pul"].status == "manual"
    assert by_category["rope"].status == "ok"
    assert [a.code for a in assumptions] == ["uncatalogued_budget_category"]
    assert "strict_pul" in assumptions[0].detail


def test_a_confirmed_zero_budget_fails_on_any_pull() -> None:
    budgets = [PullBudget(id="none", weekly_caps={"rope": 0})]
    findings, _ = evaluate_week(
        [canonical(3, item(0, "rope_climb", sets=1, reps=1))], budgets, week=WEEK
    )
    assert [(f.value, f.cap, f.status) for f in findings] == [(1, 0, "fail")]
