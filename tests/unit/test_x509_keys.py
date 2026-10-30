"""Verify x509 certificate-map keys, as published for Firebase ID tokens, without network calls."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from email.message import Message
from io import BytesIO
from types import TracebackType
from urllib.error import URLError

import httpx
import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID
from pydantic import HttpUrl

from athlete_platform.api.main import create_app
from athlete_platform.config import Settings
from athlete_platform.domain.identity import IdentityUnavailable, InvalidIdentity
from athlete_platform.infrastructure import identity
from athlete_platform.infrastructure.identity import JWTIdentityVerifier

ISSUER = "https://securetoken.invalid/synthetic-project"
AUDIENCE = "synthetic-project"
KEYS_URL = "https://keys.invalid/x509"


@pytest.fixture
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def certificate(key: rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey) -> str:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def signed_token(key: rsa.RSAPrivateKey, kid: str = "synthetic", **changes: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": "synthetic",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "auth_time": int((now - timedelta(minutes=1)).timestamp()),
    }
    claims.update(changes)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


class Response:
    def __init__(self, body: bytes, cache_control: str) -> None:
        self.body = BytesIO(body)
        self.headers = Message()
        if cache_control:
            self.headers["Cache-Control"] = cache_control

    def read(self, limit: int) -> bytes:
        return self.body.read(limit)

    def __enter__(self) -> "Response":
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        return None


class Transport:
    def __init__(self, response: bytes | Exception, cache_control: str = "") -> None:
        self.response = response
        self.cache_control = cache_control
        self.calls = 0

    def open(self, request: object, *, timeout: float) -> Response:
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return Response(self.response, self.cache_control)


def install(monkeypatch: pytest.MonkeyPatch, transport: Transport) -> Transport:
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: transport)
    return transport


def certificate_map(**certificates: str) -> bytes:
    return json.dumps(certificates).encode()


def verifier() -> JWTIdentityVerifier:
    return JWTIdentityVerifier(
        ISSUER, AUDIENCE, KEYS_URL, key_format="x509", require_auth_time=True
    )


def http_response(token: str) -> tuple[int, object]:
    app = create_app(
        settings=Settings(
            database_url=None,
            oidc_issuer=ISSUER,
            oidc_audience=AUDIENCE,
            oidc_jwks_url=HttpUrl(KEYS_URL),
            oidc_key_format="x509",
            oidc_require_auth_time=True,
        )
    )

    async def request() -> tuple[int, object]:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/me/profile", headers={"Authorization": f"Bearer {token}"}
                )
                return response.status_code, response.json().get("detail")

    return asyncio.run(request())


def test_certificate_map_verifies_token(
    key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, Transport(certificate_map(synthetic=certificate(key))))
    assert verifier().verify(signed_token(key)).subject == "synthetic"


def test_api_wiring_uses_certificate_map_and_auth_time(
    key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, Transport(certificate_map(synthetic=certificate(key))))
    future = int((datetime.now(UTC) + timedelta(days=1)).timestamp())
    assert http_response(signed_token(key, auth_time=future))[0] == 401
    # A valid token passes authentication; only the unconfigured database is left to fail.
    assert http_response(signed_token(key)) == (503, "Database is not configured")


def test_cache_control_bounds_refetching(
    key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = install(
        monkeypatch,
        Transport(certificate_map(synthetic=certificate(key)), "public, max-age=3600"),
    )
    instance = verifier()
    for _ in range(3):
        instance.verify(signed_token(key))
    assert transport.calls == 1


def test_unknown_kid_refetches_once_then_is_invalid(
    key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = install(
        monkeypatch, Transport(certificate_map(synthetic=certificate(key)), "max-age=3600")
    )
    instance = verifier()
    instance.verify(signed_token(key))
    instance.keys.fetched_at -= instance.keys.min_refresh_interval  # type: ignore[attr-defined]
    with pytest.raises(InvalidIdentity):
        instance.verify(signed_token(key, kid="rotated"))
    with pytest.raises(InvalidIdentity):
        instance.verify(signed_token(key, kid="rotated"))
    assert transport.calls == 2


def test_wrong_signature_is_invalid(
    key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    install(monkeypatch, Transport(certificate_map(synthetic=certificate(other))))
    with pytest.raises(InvalidIdentity):
        verifier().verify(signed_token(key))


@pytest.mark.parametrize(
    "response",
    [
        b"<html>upstream unavailable</html>",
        b"[]",
        b"{}",
        b'{"synthetic": 1}',
        b'{"synthetic": "not a certificate"}',
        URLError("synthetic outage"),
    ],
)
def test_provider_failures_are_unavailable(
    response: bytes | Exception, key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, Transport(response))
    token = signed_token(key)
    with pytest.raises(IdentityUnavailable):
        verifier().verify(token)
    assert http_response(token) == (503, "Identity verification unavailable")


def test_non_rsa_certificate_is_unavailable(
    key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    elliptic = ec.generate_private_key(ec.SECP256R1())
    install(monkeypatch, Transport(certificate_map(synthetic=certificate(elliptic))))
    with pytest.raises(IdentityUnavailable):
        verifier().verify(signed_token(key))


@pytest.mark.parametrize(
    ("header", "expected"),
    [("", 300), ("max-age=60", 60), ("public, max-age=999999", 86400), ("no-cache", 300)],
)
def test_lifespan_parses_and_caps_max_age(header: str, expected: int) -> None:
    assert identity.lifespan(header, 300, 86400) == expected
