# module-kind: service
"""OIDC id-token binding for the OAuth authorization-code callback.

The binding is a fail-closed matrix over two independent facts -- whether
the connection is configured for OIDC, and whether the provider actually
returned an ``id_token`` -- and every asymmetry between them is a refusal.
That reasoning is self-contained and has nothing to say about the rest of
the callback, so it lives beside the handler rather than inside it.
"""

from collections.abc import Mapping
from typing import NoReturn

from synthorg.integrations.connections.models import (
    Connection,
    OAuthState,
    OAuthToken,
)
from synthorg.integrations.errors import OIDCVerificationError
from synthorg.integrations.oauth.oidc_verify import verify_id_token
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import OAUTH_FLOW_FAILED

logger = get_logger(__name__)


async def verify_oidc_binding(
    token: OAuthToken,
    *,
    conn: Connection,
    credentials: Mapping[str, str],
    oauth_state: OAuthState,
    client_id: str,
) -> None:
    """Enforce the fail-closed OIDC matrix on the exchange result.

    A connection is "OIDC" iff it carries a ``jwks_uri``. Any asymmetry
    between "configured for OIDC" and "id_token returned" is rejected so a
    downgrade (IdP silently dropping the id_token) cannot disable the
    binding, and an unverifiable id_token cannot slip through.

    Raises:
        OIDCVerificationError: On any asymmetry, on a configured
            connection missing its issuer or state nonce, or when the
            id_token itself fails verification.
    """
    jwks_uri = credentials.get("jwks_uri", "")
    if jwks_uri and not token.id_token:
        _reject(
            conn,
            "oidc_id_token_missing",
            "Connection is OIDC-configured but the provider returned no id_token",
        )
    if token.id_token and not jwks_uri:
        _reject(
            conn,
            "oidc_jwks_uri_missing",
            "Provider returned an id_token but connection has no jwks_uri",
        )
    if not (token.id_token and jwks_uri):
        return
    oidc_issuer = credentials.get("oidc_issuer", "")
    if not oidc_issuer:
        _reject(
            conn,
            "oidc_issuer_missing",
            "OIDC connection (jwks_uri set) is missing oidc_issuer",
        )
    if oauth_state.nonce is None:
        _reject(
            conn,
            "oidc_state_nonce_missing",
            "OAuth state carries no nonce; cannot bind the id_token",
        )
    try:
        await verify_id_token(
            token.id_token,
            jwks_uri=jwks_uri,
            issuer=oidc_issuer,
            client_id=client_id,
            expected_nonce=str(oauth_state.nonce),
        )
    except OIDCVerificationError:
        logger.warning(
            OAUTH_FLOW_FAILED,
            connection_name=conn.name,
            reason="oidc_id_token_verification_failed",
        )
        raise


def _reject(conn: Connection, reason: str, msg: str) -> NoReturn:
    """Report and raise one OIDC-matrix rejection.

    Raises:
        OIDCVerificationError: Always; the caller has already decided the
            binding cannot be established.
    """
    logger.warning(OAUTH_FLOW_FAILED, connection_name=conn.name, reason=reason)
    raise OIDCVerificationError(msg)


__all__ = ["verify_oidc_binding"]
