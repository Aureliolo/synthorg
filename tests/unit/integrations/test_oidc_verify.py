# mypy: disable-error-code="explicit-any"
"""OIDC id_token verification: signature, claims, and nonce binding.

These tests own a module-scope RSA keypair and stub the JWKS lookup so
no network is touched. The negative matrix is the security contract:
a bad signature, wrong issuer/audience, expired token, missing nonce,
mismatched nonce, or an HS256 algorithm-confusion attempt must all be
rejected; only a correctly-signed token whose ``nonce`` matches the
stored value is accepted.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from synthorg.integrations.errors import (
    OIDCNonceMismatchError,
    OIDCVerificationError,
)
from synthorg.integrations.oauth import oidc_verify

pytestmark = pytest.mark.unit

_ISSUER = "https://idp.example.com"
_CLIENT_ID = "client-123"
_NONCE = "n0nce-AbC-xyz"
_JWKS_URI = "https://idp.example.com/.well-known/jwks.json"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _claims(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "iss": _ISSUER,
        "aud": _CLIENT_ID,
        "sub": "user-1",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "nonce": _NONCE,
    }
    base.update(overrides)
    return base


def _sign(claims: dict[str, Any], *, key: Any = _PRIVATE_KEY) -> str:
    return jwt.encode(claims, key, algorithm="RS256")


@pytest.fixture(autouse=True)
def _stub_jwks() -> Iterator[None]:
    """Resolve the signing key to our public key without any network."""
    oidc_verify._reset_jwks_cache_for_tests()

    class _FakeJWKClient:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        def get_signing_key_from_jwt(self, _token: str) -> SimpleNamespace:
            return SimpleNamespace(key=_PRIVATE_KEY.public_key())

    with patch.object(oidc_verify, "PyJWKClient", _FakeJWKClient):
        yield
    oidc_verify._reset_jwks_cache_for_tests()


async def _verify(token: str, *, expected_nonce: str = _NONCE) -> None:
    await oidc_verify.verify_id_token(
        token,
        jwks_uri=_JWKS_URI,
        issuer=_ISSUER,
        client_id=_CLIENT_ID,
        expected_nonce=expected_nonce,
    )


async def test_valid_token_passes() -> None:
    await _verify(_sign(_claims()))


async def test_nonce_mismatch_rejected() -> None:
    with pytest.raises(OIDCNonceMismatchError):
        await _verify(_sign(_claims(nonce="attacker-nonce")))


async def test_missing_nonce_rejected() -> None:
    claims = _claims()
    del claims["nonce"]
    with pytest.raises(OIDCVerificationError):
        await _verify(_sign(claims))


async def test_bad_signature_rejected() -> None:
    with pytest.raises(OIDCVerificationError):
        await _verify(_sign(_claims(), key=_OTHER_KEY))


async def test_audience_mismatch_rejected() -> None:
    with pytest.raises(OIDCVerificationError):
        await _verify(_sign(_claims(aud="someone-else")))


async def test_issuer_mismatch_rejected() -> None:
    with pytest.raises(OIDCVerificationError):
        await _verify(_sign(_claims(iss="https://evil.example.com")))


async def test_expired_token_rejected() -> None:
    past = datetime.now(UTC) - timedelta(hours=1)
    with pytest.raises(OIDCVerificationError):
        await _verify(_sign(_claims(exp=int(past.timestamp()))))


async def test_expiry_within_leeway_accepted() -> None:
    # Expired by a few seconds: NTP skew tolerance must not reject a
    # legitimate token.
    skewed = datetime.now(UTC) - timedelta(seconds=5)
    await _verify(_sign(_claims(exp=int(skewed.timestamp()))))


async def test_hs256_algorithm_confusion_rejected() -> None:
    # Algorithm-confusion attack shape: the attacker forges an HS256
    # token (here with an arbitrary secret). The asymmetric-only
    # allowlist must reject ANY HS256 token regardless of the secret,
    # so the public-key-as-HMAC-secret variant is closed too.
    forged = jwt.encode(
        _claims(), "attacker-chosen-secret-padded-to-32+bytes", algorithm="HS256"
    )
    with pytest.raises(OIDCVerificationError):
        await _verify(forged)
