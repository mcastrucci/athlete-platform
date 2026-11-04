"""Verify signatures and registered claims with synthetic RSA keys, without network calls."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from athlete_platform.domain.identity import InvalidIdentity
from athlete_platform.infrastructure.identity import JWTIdentityVerifier


@pytest.fixture
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(key: rsa.RSAPrivateKey, **changes: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": "synthetic-athlete",
        "iss": "https://issuer.invalid",
        "aud": "athlete-api",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(changes)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "synthetic"})


def verifier(
    key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch, *, require_auth_time: bool = False
) -> JWTIdentityVerifier:
    result = JWTIdentityVerifier(
        "https://issuer.invalid",
        "athlete-api",
        "https://issuer.invalid/keys",
        require_auth_time=require_auth_time,
    )
    monkeypatch.setattr(result.keys, "key_for", lambda _: key.public_key())
    return result


def test_valid_token_yields_verified_identity(
    key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert verifier(key, monkeypatch).verify(token(key)).subject == "synthetic-athlete"


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "https://other.invalid"},
        {"aud": "other-api"},
        {"exp": 0},
        {"sub": ""},
        {"sub": None},
        {"iat": datetime.now(UTC) + timedelta(days=1)},
    ],
)
def test_invalid_claims_are_rejected(
    changes: dict[str, object], key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(InvalidIdentity):
        verifier(key, monkeypatch).verify(token(key, **changes))


def test_wrong_signature_and_unsigned_token_are_rejected(
    key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    instance = verifier(key, monkeypatch)
    with pytest.raises(InvalidIdentity):
        instance.verify(token(other))
    with pytest.raises(InvalidIdentity):
        instance.verify(jwt.encode({"sub": "forged"}, key="", algorithm="none"))


def test_auth_time_is_optional_unless_required(
    key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert verifier(key, monkeypatch).verify(token(key)).subject == "synthetic-athlete"
    past = int((datetime.now(UTC) - timedelta(minutes=1)).timestamp())
    required = verifier(key, monkeypatch, require_auth_time=True)
    assert required.verify(token(key, auth_time=past)).subject == "synthetic-athlete"


@pytest.mark.parametrize(
    "auth_time",
    [None, int((datetime.now(UTC) + timedelta(days=1)).timestamp()), True, "0", -1],
)
def test_required_auth_time_must_be_a_past_timestamp(
    auth_time: object, key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    changes = {} if auth_time is None else {"auth_time": auth_time}
    with pytest.raises(InvalidIdentity):
        verifier(key, monkeypatch, require_auth_time=True).verify(token(key, **changes))
