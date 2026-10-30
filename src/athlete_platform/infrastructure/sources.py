"""Owned source and interpretation operations, scoped to the verified issuer and subject."""

import hashlib
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from athlete_platform.application.sources import (
    DuplicateDecision,
    DuplicateStatus,
    ExtractionProvenance,
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
from athlete_platform.infrastructure.models import Athlete, User
from athlete_platform.infrastructure.source_models import InterpretationRevision, Source

IDEMPOTENCY_CONSTRAINT = "uq_sources_athlete_id"


def content_hash(text: str) -> str:
    """Exact-text digest: identical submissions match; any edit, even whitespace, does not."""
    return hashlib.sha256(text.encode()).hexdigest()


def source_view(row: Source) -> SourceView:
    return SourceView(
        id=row.id,
        athlete_id=row.athlete_id,
        kind="text",
        text=row.text,
        received_at=row.received_at,
        duplicate_of=row.duplicate_of,
        duplicate_status=DuplicateStatus(row.duplicate_status),
        interpretation_version=row.interpretation_version,
    )


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
    def __init__(self, factory: sessionmaker[Session], identity: VerifiedIdentity) -> None:
        self.factory = factory
        self.identity = identity

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

    def submit(self, athlete_id: UUID, submission: SourceSubmission) -> tuple[SourceView, bool]:
        digest = content_hash(submission.text)
        try:
            return self._insert(athlete_id, submission, digest)
        except IntegrityError as error:
            if not is_idempotency_violation(error):
                raise
        # A concurrent submission committed this key first; resolve against its stored text.
        with transaction(self.factory) as session:
            return self._replay(session, athlete_id, submission.idempotency_key, digest), False

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
            return source_view(self._source(session, athlete_id, source_id))

    def decide_duplicate(
        self, athlete_id: UUID, source_id: UUID, decision: DuplicateDecision
    ) -> SourceView:
        with transaction(self.factory) as session:
            source = self._source(session, athlete_id, source_id, lock=True)
            target = (
                DuplicateStatus.KEPT if decision.decision == "keep" else DuplicateStatus.DISCARDED
            )
            if source.duplicate_status == target:
                return source_view(source)
            if source.duplicate_status != DuplicateStatus.PENDING:
                raise SourceConflict
            source.duplicate_status = target
            session.flush()
            return source_view(source)

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
    """Worker-side writer; callers are trusted job handlers, never HTTP requests."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory

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
            if source.duplicate_status in (DuplicateStatus.PENDING, DuplicateStatus.DISCARDED):
                raise SourceConflict
            version = (source.interpretation_version or 0) + 1
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
