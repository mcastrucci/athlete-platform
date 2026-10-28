"""Profile validation does not invent missing context or accept ownership fields."""

import os

import pytest
from pydantic import HttpUrl, ValidationError

from athlete_platform.api.main import create_app
from athlete_platform.application.profiles import ProfileContent
from athlete_platform.config import Settings
from athlete_platform.domain.identity import VerifiedIdentity


class TestVerifier:
    __test__ = False

    def verify(self, token: str) -> VerifiedIdentity:
        return VerifiedIdentity("https://tests.invalid", token)


def test_missing_values_are_unknown_and_timezone_is_validated() -> None:
    content = ProfileContent(timezone="America/Argentina/Buenos_Aires")
    assert content.equipment is None
    assert content.baselines is None
    with pytest.raises(ValidationError):
        ProfileContent(timezone="Invalid/Nowhere")
    with pytest.raises(ValidationError):
        ProfileContent.model_validate({"timezone": "UTC", "owner_user_id": "forged"})


@pytest.mark.parametrize("environment", ["local", "production"])
def test_identity_override_is_rejected_outside_tests(environment: str) -> None:
    settings = Settings.model_validate({"environment": environment})
    with pytest.raises(ValueError, match="only in local test"):
        create_app(settings=settings, test_identity_verifier=TestVerifier())


def test_identity_override_is_rejected_in_cloud_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K_SERVICE", "deployed-api")
    with pytest.raises(ValueError, match="only in local test"):
        create_app(settings=Settings(environment="test"), test_identity_verifier=TestVerifier())


def test_oidc_configuration_is_atomic_and_keeps_exact_issuer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in list(os.environ):
        if key.startswith("ATHLETE_OIDC_"):
            monkeypatch.delenv(key)
    with pytest.raises(ValidationError):
        Settings(oidc_issuer="https://issuer.invalid")
    settings = Settings(
        oidc_issuer="https://issuer.invalid",
        oidc_audience="athlete-api",
        oidc_jwks_url=HttpUrl("https://issuer.invalid/keys"),
    )
    assert settings.oidc_issuer == "https://issuer.invalid"
