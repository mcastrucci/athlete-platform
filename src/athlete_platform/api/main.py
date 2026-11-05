"""API factory. Health checks never connect to PostgreSQL or a model provider."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from athlete_platform.application.evaluation import (
    MAX_EVALUATION_PAGE,
    EvaluationList,
    EvaluationNotFound,
    EvaluationRequest,
    Evaluations,
    EvaluationView,
    WeekLabel,
)
from athlete_platform.application.profiles import (
    IdentityVerifier,
    ProfileConflict,
    ProfileContent,
    ProfileNotFound,
    Profiles,
    ProfileSnapshot,
    ProfileUpdate,
)
from athlete_platform.application.sessions import (
    MAX_SESSION_PAGE,
    ConfirmationInvalid,
    ConfirmationRequest,
    ConfirmationView,
    SessionList,
    Sessions,
)
from athlete_platform.application.sources import (
    DuplicateDecision,
    InterpretationCorrection,
    InterpretationSnapshot,
    SourceConflict,
    SourceNotFound,
    Sources,
    SourceSubmission,
    SourceView,
)
from athlete_platform.config import Settings
from athlete_platform.domain.identity import IdentityUnavailable, InvalidIdentity, VerifiedIdentity
from athlete_platform.infrastructure.database import build_engine, build_session_factory
from athlete_platform.infrastructure.evaluations import PostgresEvaluations
from athlete_platform.infrastructure.identity import (
    JWTIdentityVerifier,
    UnconfiguredIdentityVerifier,
)
from athlete_platform.infrastructure.jobs import PostgresJobs
from athlete_platform.infrastructure.profiles import PostgresProfiles
from athlete_platform.infrastructure.sessions import PostgresSessions
from athlete_platform.infrastructure.sources import PostgresSources


class HealthResponse(BaseModel):
    status: str = "ok"


def create_app(
    *, settings: Settings | None = None, test_identity_verifier: IdentityVerifier | None = None
) -> FastAPI:
    settings = settings or Settings()
    if test_identity_verifier is not None and (
        settings.environment != "test" or os.getenv("K_SERVICE") or os.getenv("CLOUD_RUN_JOB")
    ):
        raise ValueError("Test identity overrides are allowed only in local test processes")
    verifier: IdentityVerifier = test_identity_verifier or UnconfiguredIdentityVerifier()
    if test_identity_verifier is None and settings.oidc_issuer is not None:
        assert settings.oidc_audience is not None and settings.oidc_jwks_url is not None
        verifier = JWTIdentityVerifier(
            settings.oidc_issuer,
            settings.oidc_audience,
            str(settings.oidc_jwks_url),
            key_format=settings.oidc_key_format,
            require_auth_time=settings.oidc_require_auth_time,
        )
    engine = build_engine(settings) if settings.database_url is not None else None
    factory = build_session_factory(engine) if engine is not None else None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if engine is not None:
                engine.dispose()

    app = FastAPI(title="Athlete Platform", version="0.1.0", lifespan=lifespan)
    bearer = HTTPBearer(auto_error=False)

    def identity(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> VerifiedIdentity:
        if credentials is None:
            raise HTTPException(
                401, "Bearer credentials required", headers={"WWW-Authenticate": "Bearer"}
            )
        try:
            return verifier.verify(credentials.credentials)
        except InvalidIdentity:
            raise HTTPException(
                401, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"}
            ) from None
        except IdentityUnavailable:
            raise HTTPException(503, "Identity verification unavailable") from None

    def profiles(principal: Annotated[VerifiedIdentity, Depends(identity)]) -> Profiles:
        if factory is None:
            raise HTTPException(503, "Database is not configured")
        return PostgresProfiles(factory, principal)

    @app.exception_handler(ProfileNotFound)
    async def not_found(request: Request, error: ProfileNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Profile not found"})

    @app.exception_handler(ProfileConflict)
    async def conflict(request: Request, error: ProfileConflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": "Profile version conflict"})

    @app.post("/v1/athletes", response_model=ProfileSnapshot, status_code=201)
    def create_profile(
        content: ProfileContent, repository: Annotated[Profiles, Depends(profiles)]
    ) -> ProfileSnapshot:
        return repository.create(content)

    @app.get("/v1/athletes/{athlete_id}/profile", response_model=ProfileSnapshot)
    def read_profile(
        athlete_id: UUID,
        repository: Annotated[Profiles, Depends(profiles)],
        version: Annotated[int | None, Query(ge=1)] = None,
    ) -> ProfileSnapshot:
        return repository.read(athlete_id, version)

    @app.get("/v1/me/profile", response_model=ProfileSnapshot)
    def read_own_profile(repository: Annotated[Profiles, Depends(profiles)]) -> ProfileSnapshot:
        return repository.read_owned()

    @app.put("/v1/athletes/{athlete_id}/profile", response_model=ProfileSnapshot)
    def update_profile(
        athlete_id: UUID,
        change: ProfileUpdate,
        repository: Annotated[Profiles, Depends(profiles)],
    ) -> ProfileSnapshot:
        return repository.update(athlete_id, change)

    def sources(principal: Annotated[VerifiedIdentity, Depends(identity)]) -> Sources:
        if factory is None:
            raise HTTPException(503, "Database is not configured")
        return PostgresSources(factory, principal, PostgresJobs(factory))

    @app.exception_handler(SourceNotFound)
    async def source_not_found(request: Request, error: SourceNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Source not found"})

    @app.exception_handler(SourceConflict)
    async def source_conflict(request: Request, error: SourceConflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": "Source conflict"})

    @app.post("/v1/athletes/{athlete_id}/sources", response_model=SourceView, status_code=201)
    def submit_source(
        athlete_id: UUID,
        submission: SourceSubmission,
        response: Response,
        repository: Annotated[Sources, Depends(sources)],
    ) -> SourceView:
        view, created = repository.submit(athlete_id, submission)
        if not created:
            response.status_code = 200
        return view

    @app.get("/v1/athletes/{athlete_id}/sources/{source_id}", response_model=SourceView)
    def read_source(
        athlete_id: UUID, source_id: UUID, repository: Annotated[Sources, Depends(sources)]
    ) -> SourceView:
        return repository.read(athlete_id, source_id)

    @app.post(
        "/v1/athletes/{athlete_id}/sources/{source_id}/duplicate-decision",
        response_model=SourceView,
    )
    def decide_duplicate(
        athlete_id: UUID,
        source_id: UUID,
        decision: DuplicateDecision,
        repository: Annotated[Sources, Depends(sources)],
    ) -> SourceView:
        return repository.decide_duplicate(athlete_id, source_id, decision)

    @app.get(
        "/v1/athletes/{athlete_id}/sources/{source_id}/interpretation",
        response_model=InterpretationSnapshot,
    )
    def read_interpretation(
        athlete_id: UUID,
        source_id: UUID,
        repository: Annotated[Sources, Depends(sources)],
        version: Annotated[int | None, Query(ge=1)] = None,
    ) -> InterpretationSnapshot:
        return repository.interpretation(athlete_id, source_id, version)

    @app.put(
        "/v1/athletes/{athlete_id}/sources/{source_id}/interpretation",
        response_model=InterpretationSnapshot,
    )
    def correct_interpretation(
        athlete_id: UUID,
        source_id: UUID,
        correction: InterpretationCorrection,
        repository: Annotated[Sources, Depends(sources)],
    ) -> InterpretationSnapshot:
        return repository.correct(athlete_id, source_id, correction)

    def sessions(principal: Annotated[VerifiedIdentity, Depends(identity)]) -> Sessions:
        if factory is None:
            raise HTTPException(503, "Database is not configured")
        return PostgresSessions(factory, principal)

    @app.exception_handler(ConfirmationInvalid)
    async def confirmation_invalid(request: Request, error: ConfirmationInvalid) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": "Interpretation cannot be confirmed", "paths": error.paths},
        )

    @app.post(
        "/v1/athletes/{athlete_id}/sources/{source_id}/confirmation",
        response_model=ConfirmationView,
        status_code=201,
    )
    def confirm_interpretation(
        athlete_id: UUID,
        source_id: UUID,
        request: ConfirmationRequest,
        response: Response,
        repository: Annotated[Sessions, Depends(sessions)],
    ) -> ConfirmationView:
        view, created = repository.confirm(athlete_id, source_id, request)
        if not created:
            response.status_code = 200
        return view

    @app.get(
        "/v1/athletes/{athlete_id}/sources/{source_id}/confirmation",
        response_model=ConfirmationView,
    )
    def read_confirmation(
        athlete_id: UUID, source_id: UUID, repository: Annotated[Sessions, Depends(sessions)]
    ) -> ConfirmationView:
        return repository.confirmation(athlete_id, source_id)

    @app.get("/v1/athletes/{athlete_id}/sessions", response_model=SessionList)
    def list_sessions(
        athlete_id: UUID,
        repository: Annotated[Sessions, Depends(sessions)],
        date_from: Annotated[date | None, Query(alias="from")] = None,
        date_to: Annotated[date | None, Query(alias="to")] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_SESSION_PAGE)] = MAX_SESSION_PAGE,
    ) -> SessionList:
        if date_from is not None and date_to is not None and date_from > date_to:
            raise HTTPException(422, "`from` must not be after `to`")
        return repository.list_sessions(athlete_id, date_from, date_to, limit)

    def evaluations(principal: Annotated[VerifiedIdentity, Depends(identity)]) -> Evaluations:
        if factory is None:
            raise HTTPException(503, "Database is not configured")
        return PostgresEvaluations(factory, principal)

    @app.exception_handler(EvaluationNotFound)
    async def evaluation_not_found(request: Request, error: EvaluationNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Evaluation not found"})

    @app.post(
        "/v1/athletes/{athlete_id}/evaluations", response_model=EvaluationView, status_code=201
    )
    def evaluate_week(
        athlete_id: UUID,
        request: EvaluationRequest,
        response: Response,
        repository: Annotated[Evaluations, Depends(evaluations)],
    ) -> EvaluationView:
        view, created = repository.evaluate(athlete_id, request)
        if not created:
            response.status_code = 200
        return view

    @app.get("/v1/athletes/{athlete_id}/evaluations", response_model=EvaluationList)
    def list_evaluations(
        athlete_id: UUID,
        week: Annotated[WeekLabel, Query()],
        repository: Annotated[Evaluations, Depends(evaluations)],
        limit: Annotated[int, Query(ge=1, le=MAX_EVALUATION_PAGE)] = MAX_EVALUATION_PAGE,
    ) -> EvaluationList:
        return repository.list_week(athlete_id, week, limit)

    @app.get("/v1/athletes/{athlete_id}/evaluations/{evaluation_id}", response_model=EvaluationView)
    def read_evaluation(
        athlete_id: UUID,
        evaluation_id: UUID,
        repository: Annotated[Evaluations, Depends(evaluations)],
    ) -> EvaluationView:
        return repository.read(athlete_id, evaluation_id)

    @app.get("/health/live", response_model=HealthResponse, tags=["operations"])
    def health() -> HealthResponse:
        """Report process liveness, not database readiness."""
        return HealthResponse()

    return app
