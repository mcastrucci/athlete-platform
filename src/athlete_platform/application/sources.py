"""Pasted-text sources and revisable interpretations; no provider or database dependency."""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator

MAX_SOURCE_CHARACTERS = 20_000

Text = Annotated[str, Field(min_length=1, max_length=2000)]
Count = Annotated[int, Field(ge=0, le=10_000)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DuplicateStatus(StrEnum):
    NONE = "none"
    PENDING = "pending"
    KEPT = "kept"
    DISCARDED = "discarded"


class SourceSubmission(Contract):
    idempotency_key: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=MAX_SOURCE_CHARACTERS)

    @field_validator("idempotency_key", "text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Must contain non-whitespace characters")
        return value


class DuplicateDecision(Contract):
    decision: Literal["keep", "discard"]


class SourceView(Contract):
    id: UUID
    athlete_id: UUID
    kind: Literal["text"]
    text: str
    received_at: datetime
    duplicate_of: UUID | None
    duplicate_status: DuplicateStatus
    interpretation_version: int | None


class Quantity(Contract):
    """A measured amount in the unit the athlete wrote; units are never converted here."""

    value: FiniteFloat = Field(ge=0)
    unit: Text


class Ambiguity(Contract):
    field: Text
    note: Text


class InterpretedItem(Contract):
    """One exercise or piece. None means unknown; an explicit 0 means zero."""

    exercise_text: Text
    exercise_id: Text | None = None
    kind: Literal["strength", "conditioning", "skill", "mobility", "other", "unknown"] = "unknown"
    prescription_text: Text | None = None
    sets: Count | None = None
    reps: Count | None = None
    rounds: Count | None = None
    load: Quantity | None = None
    duration: Quantity | None = None
    distance: Quantity | None = None
    rpe: FiniteFloat | None = Field(default=None, ge=0, le=10)
    notes: Text | None = None
    ambiguities: list[Ambiguity] = Field(default_factory=list, max_length=50)


class InterpretedSession(Contract):
    status: Literal["planned", "performed", "unknown"] = "unknown"
    session_date: date | None = None
    date_text: Text | None = None
    title: Text | None = None
    duration: Quantity | None = None
    session_rpe: FiniteFloat | None = Field(default=None, ge=0, le=10)
    items: list[InterpretedItem] = Field(default_factory=list, max_length=100)
    notes: Text | None = None
    ambiguities: list[Ambiguity] = Field(default_factory=list, max_length=50)


class InterpretationContent(Contract):
    sessions: list[InterpretedSession] = Field(default_factory=list, max_length=20)
    ambiguities: list[Ambiguity] = Field(default_factory=list, max_length=50)


class ExtractionProvenance(Contract):
    run_id: UUID
    provider: Text
    model: Text
    prompt_version: Text
    schema_version: Literal[1] = 1


class InterpretationOrigin(StrEnum):
    EXTRACTION = "extraction"
    CORRECTION = "correction"


class InterpretationSnapshot(Contract):
    source_id: UUID
    version: int
    origin: InterpretationOrigin
    based_on_version: int | None
    provenance: ExtractionProvenance | None
    created_at: datetime
    content: InterpretationContent


class InterpretationCorrection(Contract):
    expected_version: int = Field(ge=1)
    content: InterpretationContent


class SourceNotFound(Exception):
    """The source or revision is absent or belongs to another identity."""


class SourceConflict(Exception):
    """Idempotency input, duplicate decision, or expected revision conflicts with stored data."""


class Sources(Protocol):
    def submit(self, athlete_id: UUID, submission: SourceSubmission) -> tuple[SourceView, bool]: ...
    def read(self, athlete_id: UUID, source_id: UUID) -> SourceView: ...
    def decide_duplicate(
        self, athlete_id: UUID, source_id: UUID, decision: DuplicateDecision
    ) -> SourceView: ...
    def interpretation(
        self, athlete_id: UUID, source_id: UUID, version: int | None = None
    ) -> InterpretationSnapshot: ...
    def correct(
        self, athlete_id: UUID, source_id: UUID, correction: InterpretationCorrection
    ) -> InterpretationSnapshot: ...
