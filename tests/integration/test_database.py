"""Verify actual PostgreSQL commit/rollback and migration behavior."""

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from athlete_platform.infrastructure.database import build_session_factory, transaction

pytestmark = pytest.mark.integration


def test_failed_transaction_rolls_back_all_its_writes_and_session_recovers(engine: Engine) -> None:
    with engine.connect() as connection:
        connection.execute(
            text("CREATE TEMP TABLE rollback_probe (id integer PRIMARY KEY, value jsonb NOT NULL)")
        )
        connection.commit()
        factory = build_session_factory(connection)

        with transaction(factory) as session:
            session.execute(text('INSERT INTO rollback_probe VALUES (1, \'{"state": "kept"}\')'))

        with pytest.raises(IntegrityError), transaction(factory) as session:
            session.execute(text('INSERT INTO rollback_probe VALUES (2, \'{"state": "undone"}\')'))
            session.execute(text("INSERT INTO rollback_probe VALUES (1, '{}')"))

        with transaction(factory) as session:
            rows = session.execute(text("SELECT id, value FROM rollback_probe ORDER BY id")).all()
            assert rows == [(1, {"state": "kept"})]
            session.execute(text("INSERT INTO rollback_probe VALUES (3, '{}')"))

        assert connection.execute(text("SELECT count(*) FROM rollback_probe")).scalar_one() == 2


def test_loaded_attributes_remain_readable_after_commit_and_close(engine: Engine) -> None:
    class ProbeBase(DeclarativeBase):
        pass

    class Probe(ProbeBase):
        __tablename__ = "orm_probe"
        __table_args__ = {"prefixes": ["TEMPORARY"]}

        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str]

    with engine.connect() as connection:
        ProbeBase.metadata.create_all(connection)
        connection.commit()
        factory = build_session_factory(connection)
        with transaction(factory) as session:
            probe = Probe(id=1, name="synthetic")
            session.add(probe)
        assert inspect(probe).detached
        assert probe.id == 1
        assert probe.name == "synthetic"

        with transaction(factory) as session:
            loaded = session.get(Probe, 1)
            assert loaded is not None
        assert inspect(loaded).detached
        assert loaded.name == "synthetic"


def test_migrations_use_direct_dsn_and_support_round_trip(
    engine: Engine, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An unusable runtime endpoint proves Alembic selects the separate migration DSN.
    monkeypatch.setenv("ATHLETE_DATABASE_URL", "postgresql+psycopg://user:pass@127.0.0.1:1/app")
    monkeypatch.setenv("ATHLETE_MIGRATION_DATABASE_URL", database_url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.check(config)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0001_baseline"
        )
    command.downgrade(config, "base")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM alembic_version")).scalar_one() == 0
    command.upgrade(config, "head")
