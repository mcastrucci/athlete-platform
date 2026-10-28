"""Provider-neutral, fail-closed verification of configured RS256 JWTs."""

import jwt
from jwt import PyJWKClient

from athlete_platform.domain.identity import IdentityUnavailable, InvalidIdentity, VerifiedIdentity


class UnconfiguredIdentityVerifier:
    def verify(self, token: str) -> VerifiedIdentity:
        raise IdentityUnavailable("Authentication is not configured")


class JWTIdentityVerifier:
    def __init__(self, issuer: str, audience: str, jwks_url: str) -> None:
        self.issuer = issuer
        self.audience = audience
        self.keys = PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=300, timeout=5)

    def verify(self, token: str) -> VerifiedIdentity:
        try:
            if len(token) > 16384:
                raise InvalidIdentity
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise InvalidIdentity
            key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                issuer=self.issuer,
                audience=self.audience,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
            subject = claims.get("sub")
            if not isinstance(subject, str) or not subject.strip() or len(subject) > 255:
                raise InvalidIdentity
            return VerifiedIdentity(issuer=self.issuer, subject=subject)
        except jwt.PyJWKClientConnectionError:
            raise IdentityUnavailable("Identity verification is temporarily unavailable") from None
        except jwt.PyJWTError:
            raise InvalidIdentity from None
