"""Pull-budget evaluation contracts, the input manifest and its hash; no database dependency.

An evaluation is a pure function of its manifest: the confirmed interpretation versions whose
sessions fall in the evaluated span, the profile revision, the catalog version, the engine version
and the week. Equal manifests mean equal inputs, so the manifest hash identifies a result
(idempotency) and a differing current hash marks a stored result as stale.
"""

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from athlete_platform.domain.engine import ENGINE_VERSION, WINDOW_DAYS

# ENGINE_VERSION names the evaluation pipeline: the pull-budget engine plus the canonical-session
# adapter of `pull_evaluation`. Bump it when either changes what an evaluation computes.
MAX_EVALUATION_PAGE = 50
MIN_YEAR = 2000
MAX_YEAR = 2100

_WEEK = re.compile(r"([0-9]{4})-W([0-9]{2})")


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


def week_bounds(label: str) -> tuple[date, date]:
    """Return the Monday and Sunday of the ISO week ``label`` (``YYYY-Www``).

    Raises ``ValueError`` for another spelling (``2030-W9``), a week the year does not have
    (``2031-W53``), or a year outside 2000-2100.
    """
    match = _WEEK.fullmatch(label)
    if match is None:
        raise ValueError("Use an ISO week such as 2030-W10")
    year, week = int(match.group(1)), int(match.group(2))
    if not MIN_YEAR <= year <= MAX_YEAR:
        raise ValueError(f"Year must be between {MIN_YEAR} and {MAX_YEAR}")
    try:
        monday = date.fromisocalendar(year, week, 1)
    except ValueError:
        raise ValueError("That ISO week does not exist") from None
    return monday, monday + timedelta(days=6)


def span_bounds(label: str) -> tuple[date, date]:
    """Return the dates that can influence the week: six days before it through its Sunday."""
    start, end = week_bounds(label)
    return start - timedelta(days=WINDOW_DAYS - 1), end


def _valid_week(value: str) -> str:
    week_bounds(value)
    return value


WeekLabel = Annotated[str, AfterValidator(_valid_week)]
AssumptionCode = Literal[
    "no_budgets_configured",
    "no_active_budgets",
    "uncatalogued_budget_category",
    "unclassified_item",
    "unknown_exercise_id",
    "ambiguous_dose",
]


class EvaluationRequest(Contract):
    week: WeekLabel


class ConfirmationRef(Contract):
    source_id: UUID
    interpretation_version: int = Field(ge=1)


class Manifest(Contract):
    """Everything an evaluation depends on. Sessions are write-once per confirmation, so the
    confirmed (source, version) pairs identify them exactly."""

    week: WeekLabel
    profile_version: int = Field(ge=1)
    catalog_version: str
    engine_version: str
    confirmations: list[ConfirmationRef]


def build_manifest(
    week: str,
    profile_version: int,
    confirmations: Iterable[ConfirmationRef],
    *,
    catalog_version: str,
    engine_version: str = ENGINE_VERSION,
) -> Manifest:
    """Build a manifest with its confirmation set deduplicated and sorted."""
    unique = {(str(c.source_id), c.interpretation_version): c for c in confirmations}
    return Manifest(
        week=week,
        profile_version=profile_version,
        catalog_version=catalog_version,
        engine_version=engine_version,
        confirmations=[unique[key] for key in sorted(unique)],
    )


def manifest_hash(manifest: Manifest) -> str:
    """Return the SHA-256 hex digest of the canonical JSON form of ``manifest``.

    Canonical: sorted keys, no whitespace, ASCII only, and the confirmation set sorted by
    (source id, version), so the digest ignores input order and changes with any input.
    """
    document = {
        "week": manifest.week,
        "profile_version": manifest.profile_version,
        "catalog_version": manifest.catalog_version,
        "engine_version": manifest.engine_version,
        "confirmations": sorted(
            {(str(c.source_id), c.interpretation_version) for c in manifest.confirmations}
        ),
    }
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


class Assumption(Contract):
    """Something the evaluation had to assume or could not know, with where it applies."""

    code: AssumptionCode
    detail: str
    session_id: UUID | None = None
    item_position: int | None = Field(default=None, ge=0)


class Contribution(Contract):
    """One session item's share of a finding. ``pulls`` is null when its dose is unknown."""

    session_id: UUID
    item_position: int = Field(ge=0)
    session_date: date
    pulls: float | None


class Finding(Contract):
    constraint_id: str
    category: str
    kind: Literal["weekly", "rolling"]
    value: float
    cap: float
    status: Literal["ok", "fail", "manual"]
    window_start: date
    window_end: date
    unknown_dates: list[date]
    detail: str
    contributions: list[Contribution]
    catalog_version: str
    engine_version: str


class EvaluationView(Contract):
    id: UUID
    athlete_id: UUID
    week: WeekLabel
    manifest: Manifest
    manifest_hash: str
    evaluated_at: datetime
    findings: list[Finding]
    assumptions: list[Assumption]
    stale: bool


class EvaluationList(Contract):
    """One week's evaluations, newest first; ``has_more`` marks truncation."""

    evaluations: list[EvaluationView]
    has_more: bool


class EvaluationNotFound(Exception):
    """The evaluation is absent or belongs to another identity."""


class Evaluations(Protocol):
    def evaluate(self, athlete_id: UUID, request: EvaluationRequest) -> tuple[EvaluationView, bool]:
        """Evaluate a week; the flag is True only when this call stored a new evaluation."""
        ...

    def read(self, athlete_id: UUID, evaluation_id: UUID) -> EvaluationView: ...
    def list_week(
        self, athlete_id: UUID, week: str, limit: int = MAX_EVALUATION_PAGE
    ) -> EvaluationList: ...
