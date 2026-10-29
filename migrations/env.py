"""Use an explicit direct DSN for migrations; never interpolate secrets in the INI."""

from alembic import context
from sqlalchemy import create_engine, pool

from athlete_platform.config import Settings
from athlete_platform.infrastructure import models  # noqa: F401
from athlete_platform.infrastructure.database import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=Settings().database_dsn(migration=True),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(
        Settings().database_dsn(migration=True),
        poolclass=pool.NullPool,
        hide_parameters=True,
        connect_args={"connect_timeout": 10},
    )
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=target_metadata, compare_type=True
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
