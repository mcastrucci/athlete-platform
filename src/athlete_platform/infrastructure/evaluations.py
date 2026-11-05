"""Pull-budget evaluations, scoped to the verified identity.

Idempotency and races: the unique constraint on (athlete, week, manifest hash) is the arbiter.
`evaluate` computes the result from one consistent read (the confirmed session rows and their
interpretation versions come from a single statement, so the manifest always describes exactly
the rows evaluated), then inserts with `ON CONFLICT DO NOTHING`. A concurrent identical request
blocks on the conflicting insert until the winner commits, inserts nothing, and returns the
winner's row. No lock is held on the athlete row, so evaluation never blocks profile updates,
submissions or confirmations.

Staleness is not stored. Readers recompute the current manifest hash for the stored week.
"""

from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from athlete_platform.application.evaluation import (
    MAX_EVALUATION_PAGE,
    Assumption,
    ConfirmationRef,
    EvaluationList,
    EvaluationNotFound,
    EvaluationRequest,
    EvaluationView,
    Finding,
    Manifest,
    build_manifest,
    manifest_hash,
    span_bounds,
)
from athlete_platform.application.profiles import ProfileContent, PullBudget
from athlete_platform.application.pull_evaluation import evaluate_week
from athlete_platform.domain.engine import CATALOG_VERSION
from athlete_platform.domain.identity import VerifiedIdentity
from athlete_platform.infrastructure.database import transaction
from athlete_platform.infrastructure.evaluation_models import EvaluationRow
from athlete_platform.infrastructure.models import Athlete, ProfileRevision, User
from athlete_platform.infrastructure.session_models import CanonicalSession as SessionRow
from athlete_platform.infrastructure.session_models import SourceConfirmation
from athlete_platform.infrastructure.sessions import load_sessions


def _view(row: EvaluationRow, *, stale: bool) -> EvaluationView:
    return EvaluationView(
        id=row.id,
        athlete_id=row.athlete_id,
        week=row.week,
        manifest=Manifest.model_validate(row.manifest),
        manifest_hash=row.manifest_hash,
        evaluated_at=row.evaluated_at,
        findings=[Finding.model_validate(f) for f in row.findings],
        assumptions=[Assumption.model_validate(a) for a in row.assumptions],
        stale=stale,
    )


class PostgresEvaluations:
    def __init__(self, factory: sessionmaker[Session], identity: VerifiedIdentity) -> None:
        self.factory = factory
        self.identity = identity

    def _owned(self, session: Session, athlete_id: UUID) -> Athlete:
        athlete = session.scalar(
            select(Athlete)
            .join(User, Athlete.owner_user_id == User.id)
            .where(
                Athlete.id == athlete_id,
                User.issuer == self.identity.issuer,
                User.subject == self.identity.subject,
            )
        )
        if athlete is None:
            raise EvaluationNotFound
        return athlete

    @staticmethod
    def _span_rows(
        session: Session, athlete_id: UUID, first: date, last: date
    ) -> list[tuple[SessionRow, int]]:
        """Confirmed sessions in the span with the interpretation version each came from."""
        rows = session.execute(
            select(SessionRow, SourceConfirmation.interpretation_version)
            .join(SourceConfirmation, SourceConfirmation.source_id == SessionRow.source_id)
            .where(
                SessionRow.athlete_id == athlete_id,
                SourceConfirmation.athlete_id == athlete_id,
                SessionRow.session_date >= first,
                SessionRow.session_date <= last,
            )
            .order_by(SessionRow.session_date, SessionRow.source_id, SessionRow.position)
        )
        return [(row, version) for row, version in rows]

    @staticmethod
    def _refs(rows: list[tuple[SessionRow, int]]) -> list[ConfirmationRef]:
        return [
            ConfirmationRef(source_id=source_id, interpretation_version=version)
            for source_id, version in {(row.source_id, version) for row, version in rows}
        ]

    @staticmethod
    def _budgets(session: Session, athlete: Athlete) -> list[PullBudget] | None:
        revision = session.get(ProfileRevision, (athlete.id, athlete.version))
        if revision is None:
            return None
        return ProfileContent.model_validate(revision.content).pull_budgets

    def _current_hash(self, session: Session, athlete: Athlete, week: str) -> str:
        first, last = span_bounds(week)
        rows = self._span_rows(session, athlete.id, first, last)
        manifest = build_manifest(
            week, athlete.version, self._refs(rows), catalog_version=CATALOG_VERSION
        )
        return manifest_hash(manifest)

    @staticmethod
    def _by_hash(
        session: Session, athlete_id: UUID, week: str, digest: str
    ) -> EvaluationRow | None:
        return session.scalar(
            select(EvaluationRow).where(
                EvaluationRow.athlete_id == athlete_id,
                EvaluationRow.week == week,
                EvaluationRow.manifest_hash == digest,
            )
        )

    def evaluate(self, athlete_id: UUID, request: EvaluationRequest) -> tuple[EvaluationView, bool]:
        week = request.week
        first, last = span_bounds(week)
        with transaction(self.factory) as session:
            athlete = self._owned(session, athlete_id)
            rows = self._span_rows(session, athlete.id, first, last)
            manifest = build_manifest(
                week, athlete.version, self._refs(rows), catalog_version=CATALOG_VERSION
            )
            digest = manifest_hash(manifest)
            existing = self._by_hash(session, athlete.id, week, digest)
            if existing is not None:
                return _view(existing, stale=False), False
            findings, assumptions = evaluate_week(
                load_sessions(session, [row for row, _ in rows]),
                self._budgets(session, athlete),
                week=week,
            )
            inserted = session.scalar(
                insert(EvaluationRow)
                .values(
                    id=uuid4(),
                    athlete_id=athlete.id,
                    week=week,
                    manifest=manifest.model_dump(mode="json"),
                    manifest_hash=digest,
                    findings=[f.model_dump(mode="json") for f in findings],
                    assumptions=[a.model_dump(mode="json") for a in assumptions],
                )
                .on_conflict_do_nothing(index_elements=["athlete_id", "week", "manifest_hash"])
                .returning(EvaluationRow.id)
            )
            if inserted is None:
                # A concurrent identical request committed first; return its row.
                winner = self._by_hash(session, athlete.id, week, digest)
                assert winner is not None
                return _view(winner, stale=False), False
            stored = session.get(EvaluationRow, inserted)
            assert stored is not None
            session.refresh(stored)
            return _view(stored, stale=False), True

    def read(self, athlete_id: UUID, evaluation_id: UUID) -> EvaluationView:
        with transaction(self.factory) as session:
            athlete = self._owned(session, athlete_id)
            row = session.scalar(
                select(EvaluationRow).where(
                    EvaluationRow.id == evaluation_id, EvaluationRow.athlete_id == athlete.id
                )
            )
            if row is None:
                raise EvaluationNotFound
            stale = self._current_hash(session, athlete, row.week) != row.manifest_hash
            return _view(row, stale=stale)

    def list_week(
        self, athlete_id: UUID, week: str, limit: int = MAX_EVALUATION_PAGE
    ) -> EvaluationList:
        bound = max(1, min(limit, MAX_EVALUATION_PAGE))
        with transaction(self.factory) as session:
            athlete = self._owned(session, athlete_id)
            rows = list(
                session.scalars(
                    select(EvaluationRow)
                    .where(EvaluationRow.athlete_id == athlete.id, EvaluationRow.week == week)
                    .order_by(EvaluationRow.evaluated_at.desc(), EvaluationRow.id.desc())
                    .limit(bound + 1)
                )
            )
            current = self._current_hash(session, athlete, week) if rows else ""
            return EvaluationList(
                evaluations=[
                    _view(row, stale=row.manifest_hash != current) for row in rows[:bound]
                ],
                has_more=len(rows) > bound,
            )
