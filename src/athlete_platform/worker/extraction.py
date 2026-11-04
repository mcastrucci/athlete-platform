"""Job handler that turns one text source into its first interpretation revision."""

from collections.abc import Callable
from uuid import UUID

from pydantic import JsonValue, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from athlete_platform.application.extraction import (
    ExtractionOutput,
    ExtractionRequest,
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


def extraction_handler(factory: sessionmaker[Session], extractor: Extractor) -> Handler:
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
        if not renew():
            raise TransientJobError("Lease lost before the provider call")
        try:
            output = extractor.extract(
                ExtractionRequest(source_id=source_id, text=target.text, timezone=target.timezone)
            )
            provenance, content = validated(lease, output)
        except TransientExtractionError as error:
            raise TransientJobError from error
        except (InvalidExtractionOutput, ValidationError) as error:
            raise InvalidOutputError from error
        # The provider call may have outlived the lease; a stale worker must not write.
        if not renew():
            raise TransientJobError("Lease lost during the provider call")
        snapshot = results.record(source_id, provenance, content)
        return {"interpretation_version": snapshot.version}

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
