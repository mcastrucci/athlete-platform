"""API factory. Health checks never connect to PostgreSQL or a model provider."""

from fastapi import FastAPI
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str = "ok"


def create_app() -> FastAPI:
    app = FastAPI(title="Athlete Platform", version="0.1.0")

    @app.get("/healthz", response_model=HealthResponse, tags=["operations"])
    def health() -> HealthResponse:
        """Report process liveness, not database readiness."""
        return HealthResponse()

    return app
