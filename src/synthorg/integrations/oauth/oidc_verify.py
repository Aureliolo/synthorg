"""OIDC ID-token verification for the authorization-code flow.

Verifies the provider's ``id_token`` so the OAuth callback can prove
the returned token was minted for *this* flow:

1. The signature is checked against the provider's JWKS (asymmetric
   algorithms only; an HS256 token is rejected outright to close the
   public-key-as-HMAC-secret confusion attack).
2. ``iss`` and ``aud`` are validated so a token from another IdP or
   for another client cannot be replayed here.
3. ``exp`` / ``iat`` are required (with a small skew leeway).
4. The ``nonce`` claim is compared, constant-time, against the
   single-use nonce bound at flow start.

Any failure raises :class:`OIDCVerificationError` (a
``nonce`` mismatch raises the :class:`OIDCNonceMismatchError`
subtype) so the callback fails closed.
"""

import asyncio
import hmac
from functools import lru_cache
from typing import Final

import jwt
from jwt import PyJWKClient

from synthorg.integrations.errors import (
    OIDCNonceMismatchError,
    OIDCVerificationError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    OAUTH_OIDC_VERIFICATION_FAILED,
)

logger = get_logger(__name__)

# Asymmetric signature algorithms only. ``HS256`` / ``none`` are
# deliberately excluded: accepting HMAC here would let an attacker
# sign a token with the (public) JWKS key bytes as the shared secret.
_ALLOWED_ALGORITHMS: Final[tuple[str, ...]] = (
    "RS256",
    "RS384",
    "RS512",
    "ES256",
    "ES384",
    "ES512",
    "PS256",
    "PS384",
    "PS512",
)

# Clock-skew tolerance for ``exp`` / ``iat`` so a transient NTP skew
# does not reject a legitimate token.
_LEEWAY_SECONDS: Final[int] = 60

# Bounded JWKS fetch so a slow/hung IdP cannot stall the callback path.
_JWKS_HTTP_TIMEOUT_SECONDS: Final[float] = 10.0

# Cap on distinct JWKS URIs whose ``PyJWKClient`` is retained. An
# unbounded process-global map keyed by ``jwks_uri`` would grow for the
# lifetime of the process under connection churn (and hold stale
# clients forever); the LRU eviction bounds memory while still reusing
# clients for the small set of providers in active use.
_JWKS_CLIENT_CACHE_MAX_SIZE: Final[int] = 128


@lru_cache(maxsize=_JWKS_CLIENT_CACHE_MAX_SIZE)
def _jwks_client_for(jwks_uri: str) -> PyJWKClient:
    """Return a bounded LRU-cached ``PyJWKClient`` for *jwks_uri*.

    ``lru_cache`` is thread-safe (CPython holds an internal lock around
    the cache lookup/insert), so concurrent callbacks racing on the
    same URI share one client. Each ``PyJWKClient`` caches the fetched
    signing keys internally, so callbacks do not refetch the JWKS every
    time.
    """
    return PyJWKClient(jwks_uri, timeout=_JWKS_HTTP_TIMEOUT_SECONDS)


def _reset_jwks_cache_for_tests() -> None:
    """Drop the cached JWKS clients (test isolation helper)."""
    _jwks_client_for.cache_clear()


async def verify_id_token(
    id_token: str,
    *,
    jwks_uri: str,
    issuer: str,
    client_id: str,
    expected_nonce: str,
) -> None:
    """Verify *id_token* and assert its ``nonce`` matches.

    Args:
        id_token: Compact-JWS ID token from the token response.
        jwks_uri: The provider's JWKS endpoint.
        issuer: Expected ``iss`` claim (the provider's issuer URL).
        client_id: Expected ``aud`` claim (this connection's client).
        expected_nonce: The single-use nonce bound at flow start.

    Raises:
        OIDCNonceMismatchError: The ``nonce`` claim did not match.
        OIDCVerificationError: Signature, issuer, audience, expiry,
            or required-claim verification failed (fail closed).
    """
    client = _jwks_client_for(jwks_uri)
    try:
        # ``get_signing_key_from_jwt`` does blocking HTTP (urllib);
        # off-load it so the asyncio callback handler is not stalled.
        signing_key = await asyncio.to_thread(client.get_signing_key_from_jwt, id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=list(_ALLOWED_ALGORITHMS),
            audience=client_id,
            issuer=issuer,
            leeway=_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "nonce"]},
        )
    except (jwt.exceptions.PyJWTError, jwt.exceptions.PyJWKClientError, OSError) as exc:
        # Dedicated forensic event: the caller only logs the generic
        # OAUTH_FLOW_FAILED for the whole callback, which cannot
        # distinguish a signature/issuer/expiry rejection from a
        # downstream failure. Scrubbed per the secret-log policy.
        logger.warning(
            OAUTH_OIDC_VERIFICATION_FAILED,
            reason="signature_or_claim",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"OIDC id_token verification failed: {safe_error_description(exc)}"
        raise OIDCVerificationError(msg) from exc

    claim_nonce = claims.get("nonce")
    if not isinstance(claim_nonce, str) or not hmac.compare_digest(
        claim_nonce, expected_nonce
    ):
        # A nonce mismatch is the replay/injection signal; record it
        # distinctly so a forensic reader can separate "attacker
        # replayed an id_token" from a benign verification fault.
        logger.warning(
            OAUTH_OIDC_VERIFICATION_FAILED,
            reason="nonce_mismatch",
        )
        msg = "OIDC id_token nonce does not match the flow nonce"
        raise OIDCNonceMismatchError(msg)


__all__ = ["verify_id_token"]
