"""Reject common accidental targets before destructive migration tests connect."""

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


def database_identity(url: URL) -> tuple[str, int, str]:
    """Ignore credentials and connection options when comparing database targets."""
    return ((url.host or "").lower().rstrip("."), url.port or 5432, url.database or "")


def validate_test_database(value: str | None, runtime: str | None) -> str:
    if not value:
        raise ValueError("Set TEST_DATABASE_URL to a disposable PostgreSQL database")
    try:
        target = make_url(value)
        protected = make_url(runtime) if runtime else None
    except (ArgumentError, ValueError):
        raise ValueError("Invalid database URL in test configuration") from None
    if (
        target.drivername != "postgresql+psycopg"
        or not target.host
        or not (target.database or "").endswith("_test")
    ):
        raise ValueError("TEST_DATABASE_URL must name a PostgreSQL database ending in _test")
    if protected and database_identity(target) == database_identity(protected):
        raise ValueError("TEST_DATABASE_URL must target a different database from runtime")
    return value
