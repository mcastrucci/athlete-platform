"""Provider-neutral extraction contract; no web, database, or model-provider dependency."""

from typing import Annotated, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from athlete_platform.application.sources import DuplicateStatus, InterpretationContent

Label = Annotated[str, Field(min_length=1, max_length=2000)]


def extraction_job_key(source_id: UUID) -> str:
    """Queue idempotency key: one extraction job per source."""
    return f"extract-source:{source_id}"


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    source_id: UUID
    text: str
    timezone: str | None


class ExtractionUsage(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider: Label
    model: Label
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ExtractionOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    content: InterpretationContent
    usage: ExtractionUsage
    prompt_version: Label


class TransientExtractionError(Exception):
    """The provider failed in a way a later attempt may fix (timeout, overload, rate limit)."""


class InvalidExtractionOutput(Exception):
    """The provider answered, but not with output that satisfies the interpretation schema."""


class Extractor(Protocol):
    def extract(self, request: ExtractionRequest) -> ExtractionOutput:
        """Interpret one source. Raise the errors above; never persist anything."""
        ...


class ExtractionTarget(BaseModel):
    """What a worker may know about a source before calling a provider."""

    model_config = ConfigDict(frozen=True)
    source_id: UUID
    athlete_id: UUID
    text: str
    timezone: str | None
    duplicate_status: DuplicateStatus
    interpretation_version: int | None
    # Version of the revision written by the asking run, if any.
    recorded_version: int | None
