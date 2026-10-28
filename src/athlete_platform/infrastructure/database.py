"""Explicit SQLAlchemy engine and short-lived transaction construction."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from athlete_platform.config import Settings


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def build_engine(settings: Settings) -> Engine:
    """Construct a lazy engine; callers own disposal at process shutdown."""
    return create_engine(
        settings.database_dsn(),
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 10},
    )


def build_session_factory(bind: Engine | Connection) -> sessionmaker[Session]:
    """Retain loaded attributes after commit; unloaded relationships still need a session."""
    return sessionmaker(bind=bind, class_=Session, expire_on_commit=False)


@contextmanager
def transaction(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Commit on success, roll back on any error, and always close the session."""
    with factory.begin() as session:
        yield session
