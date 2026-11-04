"""Provider-neutral extraction contract; no web, database, or model-provider dependency."""

from decimal import Decimal
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
    """The provider failed in a way a later attempt may fix (timeout, overload, rate limit).

    `outcome_unknown` is true unless the adapter knows the provider did not process the request
    (for example a rate-limit rejection). An unknown outcome may still have been billed, so the
    budget gate keeps its reservation.
    """

    def __init__(self, message: str = "", *, outcome_unknown: bool = True) -> None:
        super().__init__(message)
        self.outcome_unknown = outcome_unknown


class InvalidExtractionOutput(Exception):
    """The provider answered, but not with output that satisfies the interpretation schema.

    `usage` is set when the response reported billable usage, so spend can be reconciled even
    though the output is unusable.
    """

    def __init__(self, message: str = "", *, usage: "ExtractionUsage | None" = None) -> None:
        super().__init__(message)
        self.usage = usage


class ExtractionConfigurationError(Exception):
    """The provider rejected the request itself (credentials, permission, model, request shape).

    Retrying cannot help until an operator fixes configuration, so the job fails terminally.
    The provider definitely did not process the request.
    """


class CostEstimate(BaseModel):
    """Worst-case cost of one extraction call, used to reserve budget before the call."""

    model_config = ConfigDict(frozen=True)
    provider: Label
    model: Label
    usd: Decimal = Field(ge=0)


class Extractor(Protocol):
    def extract(self, request: ExtractionRequest) -> ExtractionOutput:
        """Interpret one source. Raise the errors above; never persist anything."""
        ...


class BudgetedExtractor(Extractor, Protocol):
    def estimate(self, request: ExtractionRequest) -> CostEstimate:
        """Worst-case cost of `extract(request)`; pure, no provider call."""
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
