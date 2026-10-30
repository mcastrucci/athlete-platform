"""Provider-neutral, fail-closed verification of configured RS256 JWTs."""

import json
import math
import re
import threading
import time
import urllib.request
from typing import Literal, Protocol

import jwt
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt import PyJWKClient

from athlete_platform.domain.identity import IdentityUnavailable, InvalidIdentity, VerifiedIdentity

KeyFormat = Literal["jwks", "x509"]


class SigningKeys(Protocol):
    def key_for(self, kid: str) -> RSAPublicKey:
        """Return the configured key for kid; unknown ids raise InvalidIdentity."""
        ...


class ProviderJWKClient(PyJWKClient):
    def get_signing_keys(self, refresh: bool = False) -> list[jwt.PyJWK]:
        """Classify download/parse/key-set failures before token key matching."""
        try:
            return super().get_signing_keys(refresh)
        except (jwt.PyJWTError, ValueError, TypeError):
            raise IdentityUnavailable("Identity verification is temporarily unavailable") from None


class JWKSKeys:
    def __init__(self, url: str) -> None:
        self.client = ProviderJWKClient(url, cache_jwk_set=True, lifespan=300, timeout=5)

    def key_for(self, kid: str) -> RSAPublicKey:
        try:
            key = self.client.get_signing_key(kid).key
        except jwt.PyJWTError:
            raise InvalidIdentity from None
        if not isinstance(key, RSAPublicKey):
            raise InvalidIdentity
        return key


class X509CertificateKeys:
    """A {kid: PEM certificate} map, as Google publishes for Firebase ID tokens."""

    default_lifespan = 300
    max_lifespan = 86400
    min_refresh_interval = 60

    def __init__(self, url: str) -> None:
        self.url = url
        self.lock = threading.Lock()
        self.keys: dict[str, RSAPublicKey] = {}
        self.expires_at = 0.0
        self.fetched_at = -math.inf

    def key_for(self, kid: str) -> RSAPublicKey:
        with self.lock:
            now = time.monotonic()
            if now >= self.expires_at:
                self.refresh(now)
            elif kid not in self.keys and now - self.fetched_at >= self.min_refresh_interval:
                # Rotation can publish a new kid before the cached map expires.
                self.refresh(now)
            key = self.keys.get(kid)
        if key is None:
            raise InvalidIdentity
        return key

    def refresh(self, now: float) -> None:
        try:
            request = urllib.request.Request(self.url, headers={"Accept": "application/json"})
            with urllib.request.build_opener().open(request, timeout=5) as response:
                body = response.read(1_048_576)
                cache_control = response.headers.get("Cache-Control", "")
            certificates = json.loads(body)
            if not isinstance(certificates, dict) or not certificates:
                raise ValueError
            keys = {}
            for kid, pem in certificates.items():
                if not isinstance(kid, str) or not isinstance(pem, str):
                    raise ValueError
                key = x509.load_pem_x509_certificate(pem.encode()).public_key()
                if not isinstance(key, RSAPublicKey):
                    raise ValueError
                keys[kid] = key
        except Exception:
            raise IdentityUnavailable("Identity verification is temporarily unavailable") from None
        self.keys = keys
        self.fetched_at = now
        self.expires_at = now + lifespan(cache_control, self.default_lifespan, self.max_lifespan)


def lifespan(cache_control: str, default: int, maximum: int) -> int:
    match = re.search(r"(?:^|[,\s])max-age=(\d+)", cache_control)
    return min(int(match.group(1)), maximum) if match else default


def signing_keys(url: str, key_format: KeyFormat) -> SigningKeys:
    return X509CertificateKeys(url) if key_format == "x509" else JWKSKeys(url)


class UnconfiguredIdentityVerifier:
    def verify(self, token: str) -> VerifiedIdentity:
        raise IdentityUnavailable("Authentication is not configured")


class JWTIdentityVerifier:
    def __init__(
        self,
        issuer: str,
        audience: str,
        keys_url: str,
        *,
        key_format: KeyFormat = "jwks",
        require_auth_time: bool = False,
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self.keys = signing_keys(keys_url, key_format)
        self.require_auth_time = require_auth_time

    def verify(self, token: str) -> VerifiedIdentity:
        try:
            if len(token) > 16384:
                raise InvalidIdentity
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if header.get("alg") != "RS256" or not isinstance(kid, str):
                raise InvalidIdentity
            required = ["exp", "iat", "iss", "aud", "sub"]
            if self.require_auth_time:
                required.append("auth_time")
            claims = jwt.decode(
                token,
                self.keys.key_for(kid),
                algorithms=["RS256"],
                issuer=self.issuer,
                audience=self.audience,
                options={"require": required},
            )
            subject = claims.get("sub")
            if not isinstance(subject, str) or not subject.strip() or len(subject) > 255:
                raise InvalidIdentity
            if self.require_auth_time and not authenticated_in_past(claims.get("auth_time")):
                raise InvalidIdentity
            return VerifiedIdentity(issuer=self.issuer, subject=subject)
        except jwt.PyJWTError:
            raise InvalidIdentity from None


def authenticated_in_past(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= time.time()
    )
