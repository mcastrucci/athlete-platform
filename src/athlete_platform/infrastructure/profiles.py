"""All profile operations scope queries to the verified issuer and subject."""

from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from athlete_platform.application.profiles import (
    ProfileConflict,
    ProfileContent,
    ProfileNotFound,
    ProfileSnapshot,
    ProfileUpdate,
)
from athlete_platform.domain.identity import VerifiedIdentity
from athlete_platform.infrastructure.database import transaction
from athlete_platform.infrastructure.models import Athlete, ProfileRevision, User


def snapshot(row: ProfileRevision) -> ProfileSnapshot:
    return ProfileSnapshot(
        athlete_id=row.athlete_id,
        version=row.version,
        created_at=row.created_at,
        content=ProfileContent.model_validate(row.content),
    )


class PostgresProfiles:
    def __init__(self, factory: sessionmaker[Session], identity: VerifiedIdentity) -> None:
        self.factory = factory
        self.identity = identity

    def owned(self, session: Session, athlete_id: UUID | None) -> Athlete:
        query = (
            select(Athlete)
            .join(User, Athlete.owner_user_id == User.id)
            .where(
                User.issuer == self.identity.issuer,
                User.subject == self.identity.subject,
            )
        )
        if athlete_id is not None:
            query = query.where(Athlete.id == athlete_id)
        athlete = session.scalar(query)
        if athlete is None:
            raise ProfileNotFound
        return athlete

    def read_owned(self) -> ProfileSnapshot:
        with transaction(self.factory) as session:
            athlete = self.owned(session, None)
            row = session.get(ProfileRevision, (athlete.id, athlete.version))
            if row is None:
                raise ProfileNotFound
            return snapshot(row)

    def create(self, content: ProfileContent) -> ProfileSnapshot:
        try:
            with transaction(self.factory) as session:
                session.execute(
                    insert(User)
                    .values(id=uuid4(), issuer=self.identity.issuer, subject=self.identity.subject)
                    .on_conflict_do_nothing(index_elements=["issuer", "subject"])
                )
                user_id = session.scalars(
                    select(User.id).where(
                        User.issuer == self.identity.issuer, User.subject == self.identity.subject
                    )
                ).one()
                athlete = Athlete(id=uuid4(), owner_user_id=user_id, version=1)
                session.add(athlete)
                session.flush()
                row = ProfileRevision(
                    athlete_id=athlete.id,
                    owner_user_id=user_id,
                    version=1,
                    schema_version=1,
                    content=content.model_dump(mode="json"),
                )
                session.add(row)
                session.flush()
                result = snapshot(row)
            return result
        except IntegrityError as error:
            if getattr(error.orig, "sqlstate", None) == "23505":
                raise ProfileConflict from None
            raise

    def read(self, athlete_id: UUID, version: int | None = None) -> ProfileSnapshot:
        with transaction(self.factory) as session:
            athlete = self.owned(session, athlete_id)
            row = session.get(
                ProfileRevision, (athlete.id, version if version is not None else athlete.version)
            )
            if row is None:
                raise ProfileNotFound
            return snapshot(row)

    def update(self, athlete_id: UUID, change: ProfileUpdate) -> ProfileSnapshot:
        with transaction(self.factory) as session:
            athlete = self.owned(session, athlete_id)
            version = session.scalar(
                update(Athlete)
                .where(
                    Athlete.id == athlete.id,
                    Athlete.owner_user_id == athlete.owner_user_id,
                    Athlete.version == change.expected_version,
                )
                .values(version=Athlete.version + 1)
                .returning(Athlete.version)
            )
            if version is None:
                raise ProfileConflict
            row = ProfileRevision(
                athlete_id=athlete.id,
                owner_user_id=athlete.owner_user_id,
                version=version,
                schema_version=1,
                content=change.content.model_dump(mode="json"),
            )
            session.add(row)
            session.flush()
            return snapshot(row)
