"""Confirmation of interpretations into canonical sessions, scoped to the verified identity.

Locking: `confirm` and `PostgresSources.correct` both take `SELECT ... FOR UPDATE` on the
source row before reading or writing anything else, so they serialize per source. The athlete
row is only read (no lock), so no other lock is held when the source lock is requested and
there is no lock-order cycle with submissions, which lock the athlete row and then insert
sources.
"""

from collections import defaultdict
from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from athlete_platform.application.sessions import (
    MAX_SESSION_PAGE,
    CanonicalItem,
    CanonicalSession,
    ConfirmationInvalid,
    ConfirmationRequest,
    ConfirmationView,
    SessionList,
    confirmation_problems,
)
from athlete_platform.application.sources import (
    Ambiguity,
    DuplicateStatus,
    InterpretationContent,
    InterpretedItem,
    InterpretedSession,
    Quantity,
    SourceConflict,
    SourceNotFound,
)
from athlete_platform.domain.identity import VerifiedIdentity
from athlete_platform.infrastructure.database import transaction
from athlete_platform.infrastructure.models import Athlete, User
from athlete_platform.infrastructure.session_models import (
    CanonicalSession as SessionRow,
)
from athlete_platform.infrastructure.session_models import (
    CanonicalSessionItem,
    SourceConfirmation,
)
from athlete_platform.infrastructure.source_models import InterpretationRevision, Source


def _quantity(value: float | None, unit: str | None) -> Quantity | None:
    return None if value is None or unit is None else Quantity(value=value, unit=unit)


def _ambiguities(items: list[Ambiguity]) -> list[dict[str, str]]:
    return [{"field": a.field, "note": a.note} for a in items]


def session_row(
    athlete_id: UUID, source_id: UUID, position: int, session: InterpretedSession
) -> SessionRow:
    """Copy a validated interpreted session verbatim; no value is converted or invented."""
    assert session.session_date is not None and session.status != "unknown"
    return SessionRow(
        id=uuid4(),
        athlete_id=athlete_id,
        source_id=source_id,
        position=position,
        status=session.status,
        session_date=session.session_date,
        title=session.title,
        date_text=session.date_text,
        duration_value=session.duration.value if session.duration else None,
        duration_unit=session.duration.unit if session.duration else None,
        session_rpe=session.session_rpe,
        notes=session.notes,
        ambiguities=_ambiguities(session.ambiguities),
    )


def item_row(session_id: UUID, position: int, item: InterpretedItem) -> CanonicalSessionItem:
    return CanonicalSessionItem(
        session_id=session_id,
        position=position,
        exercise_text=item.exercise_text,
        exercise_id=item.exercise_id,
        kind=item.kind,
        prescription_text=item.prescription_text,
        sets=item.sets,
        reps=item.reps,
        rounds=item.rounds,
        load_value=item.load.value if item.load else None,
        load_unit=item.load.unit if item.load else None,
        duration_value=item.duration.value if item.duration else None,
        duration_unit=item.duration.unit if item.duration else None,
        distance_value=item.distance.value if item.distance else None,
        distance_unit=item.distance.unit if item.distance else None,
        rpe=item.rpe,
        notes=item.notes,
        ambiguities=_ambiguities(item.ambiguities),
    )


def canonical_item(row: CanonicalSessionItem) -> CanonicalItem:
    return CanonicalItem(
        position=row.position,
        exercise_text=row.exercise_text,
        exercise_id=row.exercise_id,
        kind=row.kind,  # type: ignore[arg-type]
        prescription_text=row.prescription_text,
        sets=row.sets,
        reps=row.reps,
        rounds=row.rounds,
        load=_quantity(row.load_value, row.load_unit),
        duration=_quantity(row.duration_value, row.duration_unit),
        distance=_quantity(row.distance_value, row.distance_unit),
        rpe=row.rpe,
        notes=row.notes,
        ambiguities=[Ambiguity.model_validate(a) for a in row.ambiguities],
    )


def canonical_session(row: SessionRow, items: list[CanonicalSessionItem]) -> CanonicalSession:
    return CanonicalSession(
        id=row.id,
        athlete_id=row.athlete_id,
        source_id=row.source_id,
        position=row.position,
        status=row.status,  # type: ignore[arg-type]
        session_date=row.session_date,
        date_text=row.date_text,
        title=row.title,
        duration=_quantity(row.duration_value, row.duration_unit),
        session_rpe=row.session_rpe,
        items=[canonical_item(i) for i in sorted(items, key=lambda i: i.position)],
        notes=row.notes,
        ambiguities=[Ambiguity.model_validate(a) for a in row.ambiguities],
    )


def load_sessions(session: Session, rows: list[SessionRow]) -> list[CanonicalSession]:
    """Attach items to the given session rows, keeping the order of `rows`."""
    grouped: dict[UUID, list[CanonicalSessionItem]] = defaultdict(list)
    if rows:
        for item in session.scalars(
            select(CanonicalSessionItem).where(
                CanonicalSessionItem.session_id.in_([r.id for r in rows])
            )
        ):
            grouped[item.session_id].append(item)
    return [canonical_session(row, grouped[row.id]) for row in rows]


class PostgresSessions:
    def __init__(self, factory: sessionmaker[Session], identity: VerifiedIdentity) -> None:
        self.factory = factory
        self.identity = identity

    def _owns(self, session: Session, athlete_id: UUID) -> None:
        owned = session.scalar(
            select(Athlete.id)
            .join(User, Athlete.owner_user_id == User.id)
            .where(
                Athlete.id == athlete_id,
                User.issuer == self.identity.issuer,
                User.subject == self.identity.subject,
            )
        )
        if owned is None:
            raise SourceNotFound

    def _source(
        self, session: Session, athlete_id: UUID, source_id: UUID, *, lock: bool = False
    ) -> Source:
        self._owns(session, athlete_id)
        query = select(Source).where(Source.id == source_id, Source.athlete_id == athlete_id)
        if lock:
            query = query.with_for_update()
        source = session.scalar(query)
        if source is None:
            raise SourceNotFound
        return source

    def _view(self, session: Session, confirmation: SourceConfirmation) -> ConfirmationView:
        rows = list(
            session.scalars(
                select(SessionRow)
                .where(SessionRow.source_id == confirmation.source_id)
                .order_by(SessionRow.position)
            )
        )
        return ConfirmationView(
            source_id=confirmation.source_id,
            interpretation_version=confirmation.interpretation_version,
            confirmed_at=confirmation.confirmed_at,
            sessions=load_sessions(session, rows),
        )

    def confirm(
        self, athlete_id: UUID, source_id: UUID, request: ConfirmationRequest
    ) -> tuple[ConfirmationView, bool]:
        with transaction(self.factory) as session:
            # Serializes with corrections and other confirmations of this source.
            source = self._source(session, athlete_id, source_id, lock=True)
            existing = session.get(SourceConfirmation, source.id)
            if existing is not None:
                # Same version replays the stored result; any other version is a conflict.
                if existing.interpretation_version != request.expected_version:
                    raise SourceConflict
                return self._view(session, existing), False
            if (
                source.duplicate_status in (DuplicateStatus.PENDING, DuplicateStatus.DISCARDED)
                or source.interpretation_version != request.expected_version
            ):
                # Also covers a source with no interpretation (version None) and stale versions.
                raise SourceConflict
            revision = session.get(InterpretationRevision, (source.id, request.expected_version))
            if revision is None:
                raise SourceConflict
            content = InterpretationContent.model_validate(revision.content)
            problems = confirmation_problems(content)
            if problems:
                raise ConfirmationInvalid(problems)

            confirmation = SourceConfirmation(
                source_id=source.id,
                athlete_id=source.athlete_id,
                interpretation_version=revision.version,
            )
            session.add(confirmation)
            session.flush()
            rows = [
                session_row(source.athlete_id, source.id, position, interpreted)
                for position, interpreted in enumerate(content.sessions)
            ]
            session.add_all(rows)
            session.flush()
            for row, interpreted in zip(rows, content.sessions, strict=True):
                for position, item in enumerate(interpreted.items):
                    session.add(item_row(row.id, position, item))
            session.flush()
            session.refresh(confirmation)
            return self._view(session, confirmation), True

    def confirmation(self, athlete_id: UUID, source_id: UUID) -> ConfirmationView:
        with transaction(self.factory) as session:
            source = self._source(session, athlete_id, source_id)
            confirmation = session.get(SourceConfirmation, source.id)
            if confirmation is None:
                raise SourceNotFound
            return self._view(session, confirmation)

    def list_sessions(
        self,
        athlete_id: UUID,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = MAX_SESSION_PAGE,
    ) -> SessionList:
        bound = max(1, min(limit, MAX_SESSION_PAGE))
        with transaction(self.factory) as session:
            self._owns(session, athlete_id)
            query = select(SessionRow).where(SessionRow.athlete_id == athlete_id)
            if date_from is not None:
                query = query.where(SessionRow.session_date >= date_from)
            if date_to is not None:
                query = query.where(SessionRow.session_date <= date_to)
            rows = list(
                session.scalars(
                    query.order_by(
                        SessionRow.session_date, SessionRow.source_id, SessionRow.position
                    ).limit(bound + 1)
                )
            )
            return SessionList(
                sessions=load_sessions(session, rows[:bound]), has_more=len(rows) > bound
            )
