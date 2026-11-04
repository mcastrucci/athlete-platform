"""Job handler that turns one text source into its first interpretation revision."""

from collections.abc import Callable
from typing import cast, overload
from uuid import UUID

from pydantic import JsonValue, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from athlete_platform.application.budget import BudgetGate, Reservation
from athlete_platform.application.extraction import (
    BudgetedExtractor,
    ExtractionConfigurationError,
    ExtractionOutput,
    ExtractionRequest,
    ExtractionUsage,
    Extractor,
    InvalidExtractionOutput,
    TransientExtractionError,
)
from athlete_platform.application.jobs import JobLease
from athlete_platform.application.sources import (
    DuplicateStatus,
    ExtractionProvenance,
    InterpretationContent,
)
from athlete_platform.infrastructure.sources import PostgresExtractionResults
from athlete_platform.worker.runner import Handler, InvalidOutputError, TransientJobError


class ExtractionRejected(Exception):
    """The job can never succeed; the runner stores it as a terminal handler error."""


@overload
def extraction_handler(
    factory: sessionmaker[Session], extractor: Extractor, budget: None = None
) -> Handler: ...


@overload
def extraction_handler(
    factory: sessionmaker[Session], extractor: BudgetedExtractor, budget: BudgetGate
) -> Handler: ...


def extraction_handler(
    factory: sessionmaker[Session], extractor: Extractor, budget: BudgetGate | None = None
) -> Handler:
    """Build the job handler.

    With a budget gate the handler reserves the extractor's worst-case cost before the provider
    call and settles it afterwards. Without one (tests, fakes) no spend is tracked. Reserved
    budget is kept after any outcome in which the provider may have billed the call; it is
    released only when the call certainly did not reach it.
    """
    results = PostgresExtractionResults(factory)

    def handle(lease: JobLease, renew: Callable[[], bool]) -> dict[str, JsonValue]:
        raw = lease.payload.get("source_id")
        try:
            source_id = UUID(raw) if isinstance(raw, str) else None
        except ValueError:
            source_id = None
        if source_id is None:
            raise ExtractionRejected("Payload has no valid source_id")
        # Scope by the leased job's athlete; the payload alone is never trusted.
        target = results.load(lease.job.athlete_id, source_id, lease.job.id)
        if target is None:
            raise ExtractionRejected("Source is missing or belongs to another athlete")
        if target.recorded_version is not None:
            return {"interpretation_version": target.recorded_version}
        if (
            target.duplicate_status in (DuplicateStatus.PENDING, DuplicateStatus.DISCARDED)
            or target.interpretation_version is not None
        ):
            raise ExtractionRejected("Source is not eligible for extraction")
        request = ExtractionRequest(source_id=source_id, text=target.text, timezone=target.timezone)
        reservation: Reservation | None = None
        if budget is not None:
            estimate = cast(BudgetedExtractor, extractor).estimate(request)
            # Raises BudgetExhausted before any provider call.
            reservation = budget.reserve(
                lease.job.id, provider=estimate.provider, model=estimate.model, usd=estimate.usd
            )
        if not renew():
            release(reservation)
            raise TransientJobError("Lease lost before the provider call")
        try:
            output = extractor.extract(request)
        except TransientExtractionError as error:
            if not error.outcome_unknown:
                release(reservation)
            raise TransientJobError from error
        except InvalidExtractionOutput as error:
            if error.usage is not None:
                reconcile(reservation, error.usage)
            raise InvalidOutputError from error
        except ValidationError as error:
            raise InvalidOutputError from error
        except ExtractionConfigurationError:
            # The provider rejected the request, so nothing was billed.
            release(reservation)
            raise
        # Any other exception leaves the reservation in place: the call may have been billed.
        reconcile(reservation, output.usage)
        try:
            provenance, content = validated(lease, output)
        except ValidationError as error:
            raise InvalidOutputError from error
        # The provider call may have outlived the lease; a stale worker must not write.
        if not renew():
            raise TransientJobError("Lease lost during the provider call")
        snapshot = results.record(source_id, provenance, content)
        return {"interpretation_version": snapshot.version}

    def release(reservation: Reservation | None) -> None:
        if budget is not None and reservation is not None:
            budget.release(reservation)

    def reconcile(reservation: Reservation | None, usage: ExtractionUsage) -> None:
        if budget is not None and reservation is not None:
            budget.reconcile(reservation, usage)

    return handle


def validated(
    lease: JobLease, output: ExtractionOutput
) -> tuple[ExtractionProvenance, InterpretationContent]:
    """Re-validate untrusted extractor output; raises ValidationError when malformed."""
    content = InterpretationContent.model_validate(output.content.model_dump(mode="json"))
    provenance = ExtractionProvenance(
        run_id=lease.job.id,
        provider=output.usage.provider,
        model=output.usage.model,
        prompt_version=output.prompt_version,
    )
    return provenance, content
