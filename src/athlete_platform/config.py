"""Validated environment configuration without implicit database connections."""

from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ATHLETE_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    environment: Literal["local", "test", "production"] = "local"
    database_url: SecretStr | None = None
    migration_database_url: SecretStr | None = None
    database_pool_size: int = Field(default=2, ge=1, le=20)
    database_max_overflow: int = Field(default=0, ge=0, le=20)
    database_pool_timeout: int = Field(default=10, ge=1, le=60)
    oidc_issuer: str | None = Field(default=None, min_length=1, max_length=2000)
    oidc_audience: str | None = Field(default=None, min_length=1)
    oidc_jwks_url: HttpUrl | None = None
    oidc_key_format: Literal["jwks", "x509"] = "jwks"
    oidc_require_auth_time: bool = False

    @model_validator(mode="after")
    def validate_oidc(self) -> "Settings":
        values = (self.oidc_issuer, self.oidc_audience, self.oidc_jwks_url)
        if any(v is not None for v in values):
            if not all(v is not None for v in values):
                raise ValueError("Configure issuer, audience, and JWKS URL together")
            if (self.oidc_issuer is not None and HttpUrl(self.oidc_issuer).scheme != "https") or (
                self.oidc_jwks_url is not None and self.oidc_jwks_url.scheme != "https"
            ):
                raise ValueError("OIDC issuer and JWKS endpoint require HTTPS")
        return self

    @field_validator("database_url", "migration_database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            try:
                url = make_url(value.get_secret_value())
                if url.drivername != "postgresql+psycopg" or not url.host or not url.database:
                    raise ValueError
            except Exception:
                raise ValueError("Use a PostgreSQL psycopg URL with host and database") from None
        return value

    def database_dsn(self, *, migration: bool = False) -> str:
        value = (self.migration_database_url if migration else None) or self.database_url
        if value is None:
            raise ValueError("Configure ATHLETE_DATABASE_URL before accessing PostgreSQL")
        return value.get_secret_value()
