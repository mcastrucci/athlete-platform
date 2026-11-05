"""PullBudget validation and backward compatibility of stored profiles."""

import math
from typing import Any

import pytest
from pydantic import ValidationError

from athlete_platform.application.profiles import ProfileContent, PullBudget


def budget(**overrides: Any) -> dict[str, Any]:
    return {"id": "weekly-pulls", "weekly_caps": {"strict_pull": 40}, **overrides}


def test_a_valid_budget_defaults_to_active() -> None:
    parsed = PullBudget.model_validate(budget(weekly_caps={"strict_pull": 40, "rope": 0.0}))
    assert parsed.active is True
    assert parsed.weekly_caps == {"strict_pull": 40.0, "rope": 0.0}


@pytest.mark.parametrize(
    "bad",
    [
        budget(id=""),
        budget(id="x" * 65),
        budget(weekly_caps={}),
        budget(weekly_caps={f"category_{n}": 1 for n in range(21)}),
        budget(weekly_caps={"": 1}),
        budget(weekly_caps={"x" * 65: 1}),
        budget(weekly_caps={"strict_pull": -1}),
        budget(weekly_caps={"strict_pull": math.nan}),
        budget(weekly_caps={"strict_pull": math.inf}),
        budget(weekly_caps={"strict_pull": "many"}),
        budget(athlete_id="forged"),
        budget(active="sometimes"),
    ],
)
def test_invalid_budgets_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PullBudget.model_validate(bad)


def test_boundary_sizes_are_accepted() -> None:
    PullBudget.model_validate(budget(id="x" * 64, weekly_caps={f"c{n}": 0 for n in range(20)}))


def test_profile_budget_ids_must_be_unique_and_the_list_is_bounded() -> None:
    with pytest.raises(ValidationError, match="unique"):
        ProfileContent.model_validate(
            {"timezone": "UTC", "pull_budgets": [budget(), budget(weekly_caps={"rope": 5})]}
        )
    many = [budget(id=f"budget-{n}") for n in range(50)]
    assert (
        len(
            ProfileContent.model_validate({"timezone": "UTC", "pull_budgets": many}).pull_budgets
            or []
        )
        == 50
    )
    with pytest.raises(ValidationError):
        ProfileContent.model_validate(
            {"timezone": "UTC", "pull_budgets": [*many, budget(id="one-too-many")]}
        )


def test_profiles_stored_before_the_field_existed_still_load() -> None:
    old = {"timezone": "UTC", "equipment": ["barbell"], "constraints": []}
    content = ProfileContent.model_validate(old)
    assert content.pull_budgets is None
    assert ProfileContent.model_validate(content.model_dump(mode="json")) == content


def test_free_text_constraints_are_not_parsed_into_budgets() -> None:
    content = ProfileContent.model_validate(
        {
            "timezone": "UTC",
            "constraints": [{"description": "At most 30 pull-ups per week", "kind": "hard"}],
        }
    )
    assert content.pull_budgets is None
