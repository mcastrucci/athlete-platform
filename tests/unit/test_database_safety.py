"""Database guards fail before any connection or migration is attempted."""

import pytest

from tests.database_safety import validate_test_database


@pytest.mark.parametrize(
    "value",
    [
        None,
        "not-a-url",
        "sqlite:///athlete_test",
        "postgresql+psycopg://user:secret@localhost/athlete",
        "postgresql+psycopg:///athlete_test",
    ],
)
def test_invalid_or_non_test_database_is_rejected(value: str | None) -> None:
    with pytest.raises(ValueError):
        validate_test_database(value, None)


def test_same_database_is_rejected_despite_different_credentials_and_options() -> None:
    with pytest.raises(ValueError, match="different database"):
        validate_test_database(
            "postgresql+psycopg://test:password@LOCALHOST.:5432/athlete_test?sslmode=require",
            "postgresql+psycopg://runtime:other@localhost/athlete_test",
        )


def test_distinct_test_database_is_allowed() -> None:
    value = "postgresql+psycopg://user:password@localhost/athlete_test"
    assert validate_test_database(
        value, "postgresql+psycopg://user:password@localhost/athlete"
    ) == (value)
