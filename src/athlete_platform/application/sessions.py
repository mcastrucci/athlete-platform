"""Confirmation of one interpretation version into canonical sessions; no database dependency.

Interpretations are model output and never create sessions. The only way to materialize
canonical sessions is the athlete's explicit confirmation of one revision.
"""

from datetime import date, datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field

from athlete_platform.application.sources import (
    Ambiguity,
    Contract,
    InterpretationContent,
    InterpretedItem,
    Quantity,
    Text,
)

CONFIRMABLE_STATUSES = ("planned", "performed")
MAX_SESSION_PAGE = 500


class ConfirmationRequest(Contract):
    expected_version: int = Field(ge=1)


class CanonicalItem(InterpretedItem):
    """An interpreted item exactly as confirmed, with its position in the session."""

    position: int = Field(ge=0)


class CanonicalSession(Contract):
    id: UUID
    athlete_id: UUID
    source_id: UUID
    position: int = Field(ge=0)
    status: Literal["planned", "performed"]
    session_date: date
    date_text: Text | None = None
    title: Text | None = None
    duration: Quantity | None = None
    session_rpe: float | None = None
    items: list[CanonicalItem]
    notes: Text | None = None
    ambiguities: list[Ambiguity]


class ConfirmationView(Contract):
    source_id: UUID
    interpretation_version: int
    confirmed_at: datetime
    sessions: list[CanonicalSession]


class SessionList(Contract):
    """Confirmed sessions ordered by date, source, and position; `has_more` marks truncation."""

    sessions: list[CanonicalSession]
    has_more: bool


class ConfirmationInvalid(Exception):
    """The interpretation lacks facts a canonical session requires; nothing was written."""

    def __init__(self, paths: list[str]) -> None:
        super().__init__(", ".join(paths))
        self.paths = paths


def confirmation_problems(content: InterpretationContent) -> list[str]:
    """JSON paths that block confirmation. Unknown values are reported, never invented.

    Unresolved ambiguities do not block: they are copied verbatim into the sessions.
    """
    if not content.sessions:
        return ["sessions"]
    problems: list[str] = []
    for index, session in enumerate(content.sessions):
        if session.session_date is None:
            problems.append(f"sessions[{index}].session_date")
        if session.status not in CONFIRMABLE_STATUSES:
            problems.append(f"sessions[{index}].status")
    return problems


class Sessions(Protocol):
    def confirm(
        self, athlete_id: UUID, source_id: UUID, request: ConfirmationRequest
    ) -> tuple[ConfirmationView, bool]:
        """Confirm one version; the flag is True only when this call wrote the sessions."""
        ...

    def confirmation(self, athlete_id: UUID, source_id: UUID) -> ConfirmationView: ...
    def list_sessions(
        self,
        athlete_id: UUID,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = MAX_SESSION_PAGE,
    ) -> SessionList: ...
