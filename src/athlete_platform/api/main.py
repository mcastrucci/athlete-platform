"""API factory. Health checks never connect to PostgreSQL or a model provider."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from athlete_platform.application.profiles import (
    IdentityVerifier,
    ProfileConflict,
    ProfileContent,
    ProfileNotFound,
    Profiles,
    ProfileSnapshot,
    ProfileUpdate,
)
from athlete_platform.config import Settings
from athlete_platform.domain.identity import IdentityUnavailable, InvalidIdentity, VerifiedIdentity
from athlete_platform.infrastructure.database import build_engine, build_session_factory
from athlete_platform.infrastructure.identity import (
    JWTIdentityVerifier,
    UnconfiguredIdentityVerifier,
)
from athlete_platform.infrastructure.profiles import PostgresProfiles


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

    @app.get("/health/live", response_model=HealthResponse, tags=["operations"])
    def health() -> HealthResponse:
        """Report process liveness, not database readiness."""
        return HealthResponse()

    return app
