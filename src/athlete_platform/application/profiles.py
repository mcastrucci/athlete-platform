"""Validated profile contracts and ownership-scoped repository boundary."""

from datetime import date, datetime
from typing import Annotated, Literal, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StringConstraints, field_validator

from athlete_platform.domain.identity import VerifiedIdentity

Text = Annotated[str, Field(min_length=1, max_length=2000)]
Day = Annotated[int, Field(ge=0, le=6)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Goal(Contract):
    description: Text
    priority: int = Field(ge=1)


class Availability(Contract):
    weekday: Day
    minutes: int = Field(ge=1, le=1440)


class Commitment(Contract):
    description: Text
    flexibility: Literal["fixed", "flexible"]
    programming_reference: Text | None = None
    weekdays: list[Day] | None = Field(default=None, max_length=7)
    on_date: date | None = None
    minutes: int | None = Field(default=None, ge=1, le=1440)


class Baseline(Contract):
    metric: Text
    value: FiniteFloat
    unit: Text
    observed_on: date
    source: Text


class SkillReport(Contract):
    skill: Text
    level: Literal["isolated", "consistent", "sets", "metcon"] | None = None
    notes: Text | None = None


class Constraint(Contract):
    description: Text
    kind: Literal["hard", "preference"]


BudgetId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
PullCategoryName = Annotated[str, StringConstraints(min_length=1, max_length=64)]
Cap = Annotated[FiniteFloat, Field(ge=0)]


class PullBudget(Contract):
    """Weekly caps on pull repetitions per category, evaluated against confirmed sessions.

    The free-text `constraints` are never parsed into budgets; only this structured form is
    evaluated. A cap of 0 forbids the category for the week.
    """

    id: BudgetId
    weekly_caps: dict[PullCategoryName, Cap] = Field(min_length=1, max_length=20)
    active: bool = True


class ProfileContent(Contract):
    timezone: str
    experience: Text | None = None
    goals: list[Goal] | None = Field(default=None, max_length=30)
    availability: list[Availability] | None = Field(default=None, max_length=30)
    equipment: list[Text] | None = Field(default=None, max_length=100)
    commitments: list[Commitment] | None = Field(default=None, max_length=100)
    baselines: list[Baseline] | None = Field(default=None, max_length=100)
    skills: list[SkillReport] | None = Field(default=None, max_length=100)
    constraints: list[Constraint] | None = Field(default=None, max_length=100)
    pull_budgets: list[PullBudget] | None = Field(default=None, max_length=50)

    @field_validator("pull_budgets")
    @classmethod
    def unique_budget_ids(cls, value: list[PullBudget] | None) -> list[PullBudget] | None:
        if value is not None:
            ids = [budget.id for budget in value]
            if len(set(ids)) != len(ids):
                raise ValueError("Pull budget ids must be unique")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Use a valid IANA timezone") from None
        return value


class ProfileUpdate(Contract):
    expected_version: int = Field(ge=1)
    content: ProfileContent


class ProfileSnapshot(Contract):
    athlete_id: UUID
    version: int
    created_at: datetime
    content: ProfileContent


class ProfileNotFound(Exception):
    """The resource is absent or belongs to another identity."""


class ProfileConflict(Exception):
    """The profile already exists or the expected revision is stale."""


class IdentityVerifier(Protocol):
    def verify(self, token: str) -> VerifiedIdentity: ...


class Profiles(Protocol):
    def create(self, content: ProfileContent) -> ProfileSnapshot: ...
    def read_owned(self) -> ProfileSnapshot: ...
    def read(self, athlete_id: UUID, version: int | None = None) -> ProfileSnapshot: ...
    def update(self, athlete_id: UUID, update: ProfileUpdate) -> ProfileSnapshot: ...
