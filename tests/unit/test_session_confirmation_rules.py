"""Pure confirmation rules: what blocks a confirmation and which inputs are rejected."""

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from athlete_platform.application.sessions import ConfirmationRequest, confirmation_problems
from athlete_platform.application.sources import InterpretationContent


def content(*sessions: dict[str, Any]) -> InterpretationContent:
    return InterpretationContent.model_validate({"sessions": list(sessions)})


def test_complete_sessions_have_no_problems() -> None:
    ready = content(
        {"status": "performed", "session_date": "2026-10-26"},
        {"status": "planned", "session_date": "2026-10-27"},
    )
    assert confirmation_problems(ready) == []
    assert ready.sessions[0].session_date == date(2026, 10, 26)


def test_no_sessions_is_reported_at_the_list() -> None:
    assert confirmation_problems(InterpretationContent()) == ["sessions"]


def test_missing_dates_and_unknown_status_are_reported_by_path() -> None:
    mixed = content(
        {"status": "performed", "session_date": "2026-10-26"},
        {"status": "performed"},
        {"session_date": "2026-10-28"},
        {},
    )
    assert confirmation_problems(mixed) == [
        "sessions[1].session_date",
        "sessions[2].status",
        "sessions[3].session_date",
        "sessions[3].status",
    ]


def test_ambiguities_do_not_block_confirmation() -> None:
    ambiguous = InterpretationContent.model_validate(
        {
            "sessions": [
                {
                    "status": "planned",
                    "session_date": "2026-10-26",
                    "ambiguities": [{"field": "load", "note": "Unit unclear"}],
                }
            ],
            "ambiguities": [{"field": "sessions", "note": "Second day unclear"}],
        }
    )
    assert confirmation_problems(ambiguous) == []


def test_confirmation_request_requires_a_positive_version_and_rejects_extras() -> None:
    assert ConfirmationRequest(expected_version=1).expected_version == 1
    for body in ({"expected_version": 0}, {}, {"expected_version": 1, "status": "performed"}):
        with pytest.raises(ValidationError):
            ConfirmationRequest.model_validate(body)
