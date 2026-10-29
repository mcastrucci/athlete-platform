"""Exercise real PyJWT fetch/parse/matching using a fake HTTP transport."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from urllib.error import URLError

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import HttpUrl

from athlete_platform.api.main import create_app
from athlete_platform.config import Settings
from athlete_platform.domain.identity import IdentityUnavailable, InvalidIdentity
from athlete_platform.infrastructure.identity import JWTIdentityVerifier


@pytest.fixture
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def signed_token(key: rsa.RSAPrivateKey) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "synthetic",
            "iss": "https://issuer.invalid",
            "aud": "athlete-api",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        key,
        algorithm="RS256",
        headers={"kid": "synthetic"},
    )


def key_set(key: rsa.RSAPrivateKey, kid: str = "synthetic", use: str = "sig") -> bytes:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return json.dumps({"keys": [{**jwk, "kid": kid, "use": use}]}).encode()


def stub_transport(monkeypatch: pytest.MonkeyPatch, response: bytes | Exception) -> None:
    class Opener:
        def open(self, request: object, *, timeout: float) -> BytesIO:
            if isinstance(response, Exception):
                raise response
            return BytesIO(response)

    monkeypatch.setattr("jwt.jwks_client.urllib.request.build_opener", lambda *args: Opener())


def verifier() -> JWTIdentityVerifier:
    return JWTIdentityVerifier(
        "https://issuer.invalid", "athlete-api", "https://issuer.invalid/keys"
    )


def http_status(token: str) -> int:
    app = create_app(
        settings=Settings(
            database_url=None,
            oidc_issuer="https://issuer.invalid",
            oidc_audience="athlete-api",
            oidc_jwks_url=HttpUrl("https://issuer.invalid/keys"),
        )
    )

    async def request() -> int:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/me/profile", headers={"Authorization": f"Bearer {token}"}
                )
                return response.status_code

    return asyncio.run(request())


@pytest.mark.parametrize(
    "response",
    [
        b"<html>upstream unavailable</html>",
        b"\xff",
        b"[]",
        b"{}",
        b'{"keys": []}',
        b'{"keys": "invalid"}',
        b'{"keys": [{}]}',
        URLError("synthetic outage"),
    ],
)
def test_provider_failures_return_unavailable_not_bad_credentials(
    response: bytes | Exception, key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_transport(monkeypatch, response)
    token = signed_token(key)
    with pytest.raises(IdentityUnavailable):
        verifier().verify(token)
    assert http_status(token) == 503


def test_key_set_without_signing_keys_is_unavailable(
    key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_transport(monkeypatch, key_set(key, use="enc"))
    token = signed_token(key)
    with pytest.raises(IdentityUnavailable):
        verifier().verify(token)
    assert http_status(token) == 503


def test_unknown_kid_remains_invalid_identity(
    key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_transport(monkeypatch, key_set(key, kid="different-key"))
    token = signed_token(key)
    with pytest.raises(InvalidIdentity):
        verifier().verify(token)
    assert http_status(token) == 401


def test_wrong_signature_remains_invalid_identity(
    key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    stub_transport(monkeypatch, key_set(other))
    token = signed_token(key)
    with pytest.raises(InvalidIdentity):
        verifier().verify(token)
    assert http_status(token) == 401


def test_usable_key_set_verifies_token(
    key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_transport(monkeypatch, key_set(key))
    assert verifier().verify(signed_token(key)).subject == "synthetic"
