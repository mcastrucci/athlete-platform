"""Owned source and interpretation operations, scoped to the verified issuer and subject."""

import hashlib
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from athlete_platform.application.extraction import ExtractionTarget, extraction_job_key
from athlete_platform.application.jobs import QueueFull
from athlete_platform.application.sources import (
    DuplicateDecision,
    DuplicateStatus,
    ExtractionProvenance,
    ExtractionStatus,
    InterpretationContent,
    InterpretationCorrection,
    InterpretationOrigin,
    InterpretationSnapshot,
    SourceConflict,
    SourceNotFound,
    SourceSubmission,
    SourceView,
)
from athlete_platform.domain.identity import VerifiedIdentity
from athlete_platform.infrastructure.database import transaction
from athlete_platform.infrastructure.job_models import ExtractionJob
from athlete_platform.infrastructure.jobs import PostgresJobs
from athlete_platform.infrastructure.models import Athlete, ProfileRevision, User
from athlete_platform.infrastructure.source_models import InterpretationRevision, Source

IDEMPOTENCY_CONSTRAINT = "uq_sources_athlete_id"


def content_hash(text: str) -> str:
    """Exact-text digest: identical submissions match; any edit, even whitespace, does not."""
    return hashlib.sha256(text.encode()).hexdigest()


def source_view(row: Source, extraction: ExtractionStatus | None = None) -> SourceView:
    return SourceView(
        id=row.id,
        athlete_id=row.athlete_id,
        kind="text",
        text=row.text,
        received_at=row.received_at,
        duplicate_of=row.duplicate_of,
        duplicate_status=DuplicateStatus(row.duplicate_status),
        interpretation_version=row.interpretation_version,
        extraction=extraction,
    )


def extraction_status(job_id: UUID, state: str, error_code: str | None) -> ExtractionStatus:
    return ExtractionStatus.model_validate(
        {"job_id": job_id, "state": state, "error_code": error_code}
    )


def read_extraction(session: Session, athlete_id: UUID, source_id: UUID) -> ExtractionStatus | None:
    job = session.scalar(
        select(ExtractionJob).where(
            ExtractionJob.athlete_id == athlete_id,
            ExtractionJob.idempotency_key == extraction_job_key(source_id),
        )
    )
    return None if job is None else extraction_status(job.id, job.state, job.error_code)


def interpretation_snapshot(row: InterpretationRevision) -> InterpretationSnapshot:
    return InterpretationSnapshot(
        source_id=row.source_id,
        version=row.version,
        origin=InterpretationOrigin(row.origin),
        based_on_version=row.based_on_version,
        provenance=ExtractionProvenance.model_validate(row.provenance) if row.provenance else None,
        created_at=row.created_at,
        content=InterpretationContent.model_validate(row.content),
    )


def is_idempotency_violation(error: IntegrityError) -> bool:
    diag = getattr(error.orig, "diag", None)
    return (
        getattr(error.orig, "sqlstate", None) == "23505"
        and getattr(diag, "constraint_name", None) == IDEMPOTENCY_CONSTRAINT
    )


class PostgresSources:
    def __init__(
        self,
        factory: sessionmaker[Session],
        identity: VerifiedIdentity,
        jobs: PostgresJobs | None = None,
    ) -> None:
        self.factory = factory
        self.identity = identity
        # Without a queue (tests, tools) sources are stored but never scheduled for extraction.
        self.jobs = jobs

    def _athlete(self, session: Session, athlete_id: UUID, *, lock: bool = False) -> Athlete:
        query = (
            select(Athlete)
            .join(User, Athlete.owner_user_id == User.id)
            .where(
                Athlete.id == athlete_id,
                User.issuer == self.identity.issuer,
                User.subject == self.identity.subject,
            )
        )
        if lock:
            query = query.with_for_update(of=Athlete)
        athlete = session.scalar(query)
        if athlete is None:
            raise SourceNotFound
        return athlete

    def _source(
        self, session: Session, athlete_id: UUID, source_id: UUID, *, lock: bool = False
    ) -> Source:
        self._athlete(session, athlete_id)
        query = select(Source).where(Source.id == source_id, Source.athlete_id == athlete_id)
        if lock:
            query = query.with_for_update()
        source = session.scalar(query)
        if source is None:
            raise SourceNotFound
        return source

    def _schedule(self, source: SourceView) -> SourceView:
        """Ensure extraction is queued after the source committed; safe to repeat.

        Runs outside the source transaction, so a failed or full queue never loses the
        stored source, and a client retry of the same submission heals a lost enqueue.
        """
        wanted = source.duplicate_status in (DuplicateStatus.NONE, DuplicateStatus.KEPT)
        if wanted and self.jobs is not None:
            try:
                job = self.jobs.enqueue(
                    self.identity,
                    source.athlete_id,
                    extraction_job_key(source.id),
                    {"source_id": str(source.id)},
                )
            except QueueFull:
                status = ExtractionStatus(job_id=None, state="not_queued", error_code="queue_full")
                return source.model_copy(update={"extraction": status})
            code = job.error_code.value if job.error_code else None
            status = extraction_status(job.id, job.state.value, code)
            return source.model_copy(update={"extraction": status})
        with transaction(self.factory) as session:
            return source.model_copy(
                update={"extraction": read_extraction(session, source.athlete_id, source.id)}
            )

    def submit(self, athlete_id: UUID, submission: SourceSubmission) -> tuple[SourceView, bool]:
        digest = content_hash(submission.text)
        try:
            source, created = self._insert(athlete_id, submission, digest)
            return self._schedule(source), created
        except IntegrityError as error:
            if not is_idempotency_violation(error):
                raise
        # A concurrent submission committed this key first; resolve against its stored text.
        with transaction(self.factory) as session:
            replayed = self._replay(session, athlete_id, submission.idempotency_key, digest)
        return self._schedule(replayed), False

    def _replay(self, session: Session, athlete_id: UUID, key: str, digest: str) -> SourceView:
        existing = session.scalar(
            select(Source).where(Source.athlete_id == athlete_id, Source.idempotency_key == key)
        )
        if existing is None:
            raise SourceNotFound
        if existing.content_hash != digest:
            raise SourceConflict
        return source_view(existing)

    def _insert(
        self, athlete_id: UUID, submission: SourceSubmission, digest: str
    ) -> tuple[SourceView, bool]:
        with transaction(self.factory) as session:
            # Serializes submissions per athlete so duplicate detection sees earlier commits.
            athlete = self._athlete(session, athlete_id, lock=True)
            replay = session.scalar(
                select(Source.id).where(
                    Source.athlete_id == athlete_id,
                    Source.idempotency_key == submission.idempotency_key,
                )
            )
            if replay is not None:
                return self._replay(session, athlete_id, submission.idempotency_key, digest), False
            earlier = session.scalar(
                select(Source.id)
                .where(
                    Source.athlete_id == athlete_id,
                    Source.content_hash == digest,
                    Source.duplicate_status != DuplicateStatus.DISCARDED,
                )
                .order_by(Source.received_at.desc(), Source.id)
                .limit(1)
            )
            row = Source(
                id=uuid4(),
                athlete_id=athlete_id,
                owner_user_id=athlete.owner_user_id,
                idempotency_key=submission.idempotency_key,
                kind="text",
                text=submission.text,
                content_hash=digest,
                duplicate_of=earlier,
                duplicate_status=(
                    DuplicateStatus.NONE if earlier is None else DuplicateStatus.PENDING
                ),
                interpretation_version=None,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return source_view(row), True

    def read(self, athlete_id: UUID, source_id: UUID) -> SourceView:
        with transaction(self.factory) as session:
            source = self._source(session, athlete_id, source_id)
            return source_view(source, read_extraction(session, athlete_id, source_id))

    def decide_duplicate(
        self, athlete_id: UUID, source_id: UUID, decision: DuplicateDecision
    ) -> SourceView:
        with transaction(self.factory) as session:
            source = self._source(session, athlete_id, source_id, lock=True)
            target = (
                DuplicateStatus.KEPT if decision.decision == "keep" else DuplicateStatus.DISCARDED
            )
            if source.duplicate_status != target:
                if source.duplicate_status != DuplicateStatus.PENDING:
                    raise SourceConflict
                source.duplicate_status = target
                session.flush()
            decided = source_view(source)
        # A kept source is scheduled like a fresh one; repeating the decision heals a lost enqueue.
        return self._schedule(decided)

    def interpretation(
        self, athlete_id: UUID, source_id: UUID, version: int | None = None
    ) -> InterpretationSnapshot:
        with transaction(self.factory) as session:
            source = self._source(session, athlete_id, source_id)
            wanted = version if version is not None else source.interpretation_version
            if wanted is None:
                raise SourceNotFound
            row = session.get(InterpretationRevision, (source.id, wanted))
            if row is None:
                raise SourceNotFound
            return interpretation_snapshot(row)

    def correct(
        self, athlete_id: UUID, source_id: UUID, correction: InterpretationCorrection
    ) -> InterpretationSnapshot:
        with transaction(self.factory) as session:
            source = self._source(session, athlete_id, source_id, lock=True)
            if (
                source.duplicate_status == DuplicateStatus.DISCARDED
                or source.interpretation_version != correction.expected_version
            ):
                raise SourceConflict
            version = correction.expected_version + 1
            row = InterpretationRevision(
                source_id=source.id,
                version=version,
                athlete_id=source.athlete_id,
                origin=InterpretationOrigin.CORRECTION,
                based_on_version=correction.expected_version,
                run_id=None,
                provenance=None,
                schema_version=1,
                content=correction.content.model_dump(mode="json"),
            )
            session.add(row)
            source.interpretation_version = version
            session.flush()
            session.refresh(row)
            return interpretation_snapshot(row)


class PostgresExtractionResults:
    """Worker-side writer; callers are trusted job handlers, never HTTP requests.

    Handlers should pass the job ID as the run ID so every retry of one job is idempotent.
    """

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory

    def load(self, athlete_id: UUID, source_id: UUID, run_id: UUID) -> ExtractionTarget | None:
        """Read a source for extraction only if it belongs to `athlete_id`.

        The athlete comes from the leased job, never from the payload, so a forged payload
        cannot reach another athlete's text. The transaction ends before any provider call.
        """
        with transaction(self.factory) as session:
            source = session.scalar(
                select(Source).where(Source.id == source_id, Source.athlete_id == athlete_id)
            )
            if source is None:
                return None
            athlete = session.get(Athlete, athlete_id)
            profile = (
                session.get(ProfileRevision, (athlete_id, athlete.version)) if athlete else None
            )
            zone = profile.content.get("timezone") if profile else None
            recorded = session.scalar(
                select(InterpretationRevision.version).where(
                    InterpretationRevision.source_id == source.id,
                    InterpretationRevision.run_id == run_id,
                )
            )
            return ExtractionTarget(
                source_id=source.id,
                athlete_id=source.athlete_id,
                text=source.text,
                timezone=zone if isinstance(zone, str) else None,
                duplicate_status=DuplicateStatus(source.duplicate_status),
                interpretation_version=source.interpretation_version,
                recorded_version=recorded,
            )

    def record(
        self, source_id: UUID, provenance: ExtractionProvenance, content: InterpretationContent
    ) -> InterpretationSnapshot:
        with transaction(self.factory) as session:
            source = session.scalar(select(Source).where(Source.id == source_id).with_for_update())
            if source is None:
                raise SourceNotFound
            existing = session.scalar(
                select(InterpretationRevision).where(
                    InterpretationRevision.run_id == provenance.run_id
                )
            )
            if existing is not None:
                if existing.source_id != source.id:
                    raise SourceConflict
                return interpretation_snapshot(existing)
            # Only the first extraction becomes current. A later run, such as a retry that
            # minted a new run ID, must never displace an earlier result or a correction.
            if (
                source.duplicate_status in (DuplicateStatus.PENDING, DuplicateStatus.DISCARDED)
                or source.interpretation_version is not None
            ):
                raise SourceConflict
            version = 1
            row = InterpretationRevision(
                source_id=source.id,
                version=version,
                athlete_id=source.athlete_id,
                origin=InterpretationOrigin.EXTRACTION,
                based_on_version=None,
                run_id=provenance.run_id,
                provenance=provenance.model_dump(mode="json"),
                schema_version=1,
                content=content.model_dump(mode="json"),
            )
            session.add(row)
            source.interpretation_version = version
            session.flush()
            session.refresh(row)
            return interpretation_snapshot(row)
