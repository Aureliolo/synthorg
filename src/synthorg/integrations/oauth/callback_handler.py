"""OAuth callback handler.

Provides the ``handle_oauth_callback`` function used by the
OAuth API controller to process authorization code callbacks.
"""

from typing import TYPE_CHECKING

from synthorg.integrations.connections.catalog import ConnectionCatalog  # noqa: TC001
from synthorg.integrations.errors import (
    InvalidStateError,
    TokenExchangeFailedError,
)
from synthorg.integrations.oauth.flows.authorization_code import (
    AuthorizationCodeFlow,
)
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    OAUTH_CALLBACK_RECEIVED,
    OAUTH_FLOW_COMPLETED,
    OAUTH_FLOW_FAILED,
    OAUTH_STATE_INVALID,
)
from synthorg.observability.events.settings import SETTINGS_FETCH_FAILED
from synthorg.persistence.connection_protocol import (
    OAuthStateRepository,  # noqa: TC001
)

if TYPE_CHECKING:
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)


async def resolve_oauth_http_timeout(
    config_resolver: ConfigResolver | None,
) -> float | None:
    """Best-effort lookup of the operator-tuned OAuth HTTP timeout.

    Returns ``None`` when no resolver is wired in or the lookup fails;
    callers then fall back to the ``AuthorizationCodeFlow`` default
    (:data:`integrations.oauth.flows.authorization_code._DEFAULT_HTTP_TIMEOUT_SECONDS`).
    Never propagates -- a settings outage must not break the OAuth
    callback path.
    """
    if config_resolver is None:
        return None
    from synthorg.settings.enums import SettingNamespace  # noqa: PLC0415

    try:
        return await config_resolver.get_float(
            SettingNamespace.INTEGRATIONS.value,
            "oauth_http_timeout_seconds",
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        # This is a setting-resolution fallback, not an OAuth flow
        # failure -- logging as OAUTH_FLOW_FAILED would inflate
        # failure metrics and page oncall for a benign condition.
        # Emit on the settings-fetch channel at INFO instead.
        logger.info(
            SETTINGS_FETCH_FAILED,
            namespace=SettingNamespace.INTEGRATIONS.value,
            key="oauth_http_timeout_seconds",
            error=(
                "failed to resolve oauth_http_timeout_seconds;"
                f" using flow default ({type(exc).__name__})"
            ),
        )
        return None


async def handle_oauth_callback(  # noqa: PLR0913
    *,
    state_param: str,
    code: str,
    state_repo: OAuthStateRepository,
    catalog: ConnectionCatalog,
    flow: AuthorizationCodeFlow | None = None,
    config_resolver: ConfigResolver | None = None,
) -> str:
    """Process an OAuth authorization code callback.

    Validates the state token, exchanges the code for tokens,
    persists the raw access/refresh tokens via the connection
    catalog (which writes them through the configured secret
    backend), and updates the connection's token expiry metadata.

    Args:
        state_param: The state parameter from the callback URL.
        code: The authorization code.
        state_repo: Repository for looking up OAuth states.
        catalog: Connection catalog for credential storage.
        flow: Authorization code flow instance. When ``None`` a new
            flow is constructed with the operator-tuned HTTP timeout
            resolved from ``integrations.oauth_http_timeout_seconds``
            (falling back to the flow's module default on settings
            outage).
        config_resolver: Optional ConfigResolver used to resolve the
            OAuth HTTP timeout when ``flow`` is not provided. When
            ``None`` the flow's hardcoded default is used.

    Returns:
        The connection name that was updated.

    Raises:
        InvalidStateError: If the state token is invalid or expired.
        TokenExchangeFailedError: If the code exchange fails or the
            exchange credentials (token_url / client_id /
            client_secret) are missing from the connection.
    """
    logger.info(OAUTH_CALLBACK_RECEIVED, state_prefix=state_param[:8])

    oauth_state = await state_repo.get(state_param)
    if oauth_state is None:
        logger.warning(OAUTH_STATE_INVALID, state_prefix=state_param[:8])
        msg = "Invalid or expired OAuth state token"
        raise InvalidStateError(msg)

    from datetime import UTC, datetime  # noqa: PLC0415

    if oauth_state.expires_at < datetime.now(UTC):
        await state_repo.delete(state_param)
        logger.warning(
            OAUTH_STATE_INVALID,
            state_prefix=state_param[:8],
            reason="expired",
        )
        msg = "OAuth state token expired"
        raise InvalidStateError(msg)

    await state_repo.delete(state_param)

    conn = await catalog.get_or_raise(oauth_state.connection_name)
    credentials = await catalog.get_credentials(conn.name)

    token_url = credentials.get("token_url", "")
    client_id = credentials.get("client_id", "")
    client_secret = credentials.get("client_secret", "")
    missing = [
        label
        for label, value in (
            ("token_url", token_url),
            ("client_id", client_id),
            ("client_secret", client_secret),
        )
        if not value
    ]
    if missing:
        logger.warning(
            OAUTH_FLOW_FAILED,
            connection_name=conn.name,
            missing=",".join(missing),
        )
        msg = (
            "Cannot exchange OAuth code: connection is missing "
            f"credentials: {', '.join(missing)}"
        )
        raise TokenExchangeFailedError(msg)

    if flow is not None:
        auth_flow = flow
    else:
        timeout = await resolve_oauth_http_timeout(config_resolver)
        if timeout is not None:
            auth_flow = AuthorizationCodeFlow(http_timeout_seconds=timeout)
        else:
            auth_flow = AuthorizationCodeFlow()
    try:
        token = await auth_flow.exchange_code(
            token_url=token_url,
            client_id=client_id,
            client_secret=client_secret,
            state=oauth_state,
            code=code,
            redirect_uri=oauth_state.redirect_uri,
        )
    except TokenExchangeFailedError:
        logger.warning(
            OAUTH_FLOW_FAILED,
            connection_name=conn.name,
        )
        raise

    if not token.access_token:
        logger.warning(
            OAUTH_FLOW_FAILED,
            connection_name=conn.name,
            reason="flow returned no access_token",
        )
        msg = "OAuth flow returned no access_token"
        raise TokenExchangeFailedError(msg)

    # Persist access/refresh tokens via the secret backend.
    await catalog.store_oauth_tokens(
        conn.name,
        access_token=token.access_token,
        refresh_token=token.refresh_token,
    )

    # Update connection metadata with token expiry.
    meta_updates = dict(conn.metadata)
    if token.expires_at:
        meta_updates["token_expires_at"] = token.expires_at.isoformat()
    else:
        # New token has no expiry (non-expiring grant). Drop any
        # stale ``token_expires_at`` carried over from a prior flow
        # so we do not incorrectly mark the token as expired later.
        meta_updates.pop("token_expires_at", None)
    await catalog.update(conn.name, metadata=meta_updates)

    logger.info(
        OAUTH_FLOW_COMPLETED,
        connection_name=conn.name,
    )
    return conn.name
