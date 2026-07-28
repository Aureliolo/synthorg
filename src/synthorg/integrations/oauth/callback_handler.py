"""OAuth callback handler.

Provides the ``handle_oauth_callback`` function used by the
OAuth API controller to process authorization code callbacks.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, NamedTuple

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import (
    Connection,
    OAuthState,
    OAuthToken,
)
from synthorg.integrations.errors import (
    ConnectionNotFoundError,
    InvalidStateError,
    OAuthConfigurationError,
    TokenExchangeFailedError,
)
from synthorg.integrations.oauth._callback_oidc import verify_oidc_binding
from synthorg.integrations.oauth.flows.authorization_code import (
    AuthorizationCodeFlow,
)
from synthorg.integrations.oauth.state_service import OAuthStateService
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    OAUTH_CALLBACK_RECEIVED,
    OAUTH_FLOW_COMPLETED,
    OAUTH_FLOW_FAILED,
    OAUTH_STATE_INVALID,
)
from synthorg.observability.events.settings import SETTINGS_FETCH_FAILED

if TYPE_CHECKING:
    # ConfigResolver is concrete and injected via mocks in tests; a runtime
    # import would make typeguard reject the fake.
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)


async def resolve_oauth_http_timeout(
    config_resolver: ConfigResolver | None,
) -> float | None:
    """Best-effort lookup of the operator-tuned OAuth HTTP timeout.

    Returns ``None`` when no resolver is wired in or the lookup fails;
    callers then fall back to the ``AuthorizationCodeFlow`` default
    (:data:`integrations.oauth.flows.authorization_code._DEFAULT_HTTP_TIMEOUT_SECONDS`).
    Non-critical exceptions are swallowed so a settings outage cannot
    break the OAuth callback path; interpreter-critical errors
    (``MemoryError`` / ``RecursionError``) still propagate via
    ``reraise_critical``.

    Returns:
        The operator-configured OAuth HTTP timeout in seconds, or
        ``None`` when no resolver is available or the lookup fails.
    """
    if config_resolver is None:
        return None
    from synthorg.settings.enums import SettingNamespace  # noqa: PLC0415

    try:
        return await config_resolver.get_float(
            SettingNamespace.INTEGRATIONS.value,
            "oauth_http_timeout_seconds",
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
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


async def handle_oauth_callback(
    *,
    state_param: str,
    code: str,
    state_service: OAuthStateService,
    catalog: ConnectionCatalog,
    flow: AuthorizationCodeFlow | None = None,
    config_resolver: ConfigResolver | None = None,
    clock: Clock | None = None,
) -> str:
    """Process an OAuth authorization code callback.

    Validates the state token, exchanges the code for tokens,
    persists the raw access/refresh tokens via the connection
    catalog (which writes them through the configured secret
    backend), and updates the connection's token expiry metadata.

    Args:
        state_param: The state parameter from the callback URL.
        code: The authorization code.
        state_service: Audit-aware OAuth-state facade (the callback's
            persistence-boundary delegate for get / expire /
            mark_consumed).
        catalog: Connection catalog for credential storage.
        flow: Authorization code flow instance. When ``None`` a new
            flow is constructed with the operator-tuned HTTP timeout
            resolved from ``integrations.oauth_http_timeout_seconds``
            (falling back to the flow's module default on settings
            outage).
        config_resolver: Optional ConfigResolver used to resolve the
            OAuth HTTP timeout when ``flow`` is not provided. When
            ``None`` the flow's hardcoded default is used.
        clock: Clock seam for state-expiry and consumed-at timestamps;
            defaults to ``SystemClock`` (tests inject ``FakeClock``).

    Returns:
        The connection name that was updated.

    Raises:
        ConnectionNotFoundError: If the connection was deleted between
            authorization and callback.
        InvalidStateError: If the state token is invalid or expired.
        TokenExchangeFailedError: If the code exchange fails or the
            exchange credentials (token_url / client_id /
            client_secret) are missing from the connection.
        OIDCVerificationError: If an OIDC-configured connection returns
            no ``id_token`` (or an ``id_token`` without a ``jwks_uri`` /
            ``oidc_issuer`` / state nonce), or ``verify_id_token``
            rejects the token's nonce, issuer, or audience.
    """
    logger.info(OAUTH_CALLBACK_RECEIVED, state_prefix=state_param[:8])

    effective_clock = clock or SystemClock()
    resolved = await _resolve_state(state_param, state_service, effective_clock)
    if isinstance(resolved, _ReplayedCallback):
        return resolved.connection_name
    oauth_state = resolved

    conn, credentials = await _load_connection(catalog, oauth_state)
    exchange = _exchange_credentials(conn, credentials)
    auth_flow = await _resolve_flow(flow, config_resolver)
    exchanged = await _exchange_code(
        auth_flow,
        conn=conn,
        exchange=exchange,
        oauth_state=oauth_state,
        code=code,
    )
    await verify_oidc_binding(
        exchanged.token,
        conn=conn,
        credentials=credentials,
        oauth_state=oauth_state,
        client_id=exchange.client_id,
    )
    await _persist_tokens(catalog, conn, exchanged)
    await _consume_state(
        state_service,
        state_param=state_param,
        conn=conn,
        clock=effective_clock,
    )
    return conn.name


class _ReplayedCallback(NamedTuple):
    """A redelivered callback, already consumed by an earlier delivery."""

    connection_name: str


async def _resolve_state(
    state_param: str,
    state_service: OAuthStateService,
    clock: Clock,
) -> OAuthState | _ReplayedCallback:
    """Validate the state token, or report the delivery as a replay.

    Returns:
        The live state, or a :class:`_ReplayedCallback` when an earlier
        delivery already consumed it. A redelivered callback (provider
        retry, browser back-button, CDN replay) must not re-exchange the
        authorization code: that would either fail, since codes are
        single-use at the IdP, or double-spend the code at a sibling
        worker for a malicious replay.

    Raises:
        InvalidStateError: If the token is unknown or has expired.
    """
    oauth_state = await state_service.get(NotBlankStr(state_param))
    if oauth_state is None:
        logger.warning(OAUTH_STATE_INVALID, state_prefix=state_param[:8])
        msg = "Invalid or expired OAuth state token"
        raise InvalidStateError(msg)

    if oauth_state.consumed_at is not None:
        connection_name = oauth_state.connection_name_returned
        # ``_validate_consumed_pair`` on ``OAuthState`` keeps these two
        # fields in lockstep, so a non-null ``consumed_at`` always pairs
        # with a non-null ``connection_name_returned``.
        assert connection_name is not None  # noqa: S101 -- model invariant
        logger.info(
            OAUTH_FLOW_COMPLETED,
            connection_name=str(connection_name),
            replay=True,
        )
        return _ReplayedCallback(connection_name=str(connection_name))

    if oauth_state.expires_at < clock.now():
        await state_service.expire(NotBlankStr(state_param))
        logger.warning(
            OAUTH_STATE_INVALID,
            state_prefix=state_param[:8],
            reason="expired",
        )
        msg = "OAuth state token expired"
        raise InvalidStateError(msg)
    return oauth_state


async def _load_connection(
    catalog: ConnectionCatalog,
    oauth_state: OAuthState,
) -> tuple[Connection, Mapping[str, str]]:
    """Load the connection and its credentials, as one lookup.

    Both reads resolve the same row, and either can lose it to a delete
    that lands mid-flow. They share a handler so the deletion is reported
    the same way whichever read observes it.

    Returns:
        The connection and its decrypted credentials.

    Raises:
        ConnectionNotFoundError: If the connection was deleted between
            authorization and callback.
    """
    try:
        conn = await catalog.get_or_raise(oauth_state.connection_name)
        credentials = await catalog.get_credentials(conn.name)
    except ConnectionNotFoundError:
        # The sibling failure branches all report under this event, so
        # letting this one reach only the generic request handler would
        # hide a mid-flow deletion from any OAuth-specific alerting.
        logger.warning(
            OAUTH_FLOW_FAILED,
            connection_name=str(oauth_state.connection_name),
            reason="connection_deleted_mid_flow",
        )
        raise
    return conn, credentials


class _ExchangeCredentials(NamedTuple):
    """The three fields the token exchange signs with."""

    token_url: str
    client_id: str
    client_secret: str


class _ExchangedTokens(NamedTuple):
    """An exchange result whose access token is known to be present.

    The presence check belongs with the exchange that can fail it, but the
    guarantee has to survive the hand-off to the persist step, which takes
    a required string. Carrying the narrowed value alongside the token is
    what lets both hold without either restating the other's check.
    """

    token: OAuthToken
    access_token: str


def _exchange_credentials(
    conn: Connection,
    credentials: Mapping[str, str],
) -> _ExchangeCredentials:
    """Read the exchange credentials off the connection.

    Returns:
        The three fields, all non-empty.

    Raises:
        OAuthConfigurationError: If any of them is missing, naming every
            one that is so a single round trip fixes the connection. A
            missing field is deterministic, so this is the non-retryable
            subclass: retried as a transient exchange failure it would
            burn the authorization code against a connection that cannot
            succeed until an operator edits it.
    """
    resolved = _ExchangeCredentials(
        token_url=credentials.get("token_url", ""),
        client_id=credentials.get("client_id", ""),
        client_secret=credentials.get("client_secret", ""),
    )
    missing = [name for name, value in resolved._asdict().items() if not value]
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
        raise OAuthConfigurationError(msg)
    return resolved


async def _resolve_flow(
    flow: AuthorizationCodeFlow | None,
    config_resolver: ConfigResolver | None,
) -> AuthorizationCodeFlow:
    """Return the injected flow, or one built with the operator's timeout.

    Returns:
        The flow to exchange the code through.
    """
    if flow is not None:
        return flow
    timeout = await resolve_oauth_http_timeout(config_resolver)
    if timeout is None:
        return AuthorizationCodeFlow()
    return AuthorizationCodeFlow(http_timeout_seconds=timeout)


async def _exchange_code(
    auth_flow: AuthorizationCodeFlow,
    *,
    conn: Connection,
    exchange: _ExchangeCredentials,
    oauth_state: OAuthState,
    code: str,
) -> _ExchangedTokens:
    """Trade the authorization code for tokens.

    Returns:
        The token response alongside its access token, which this is what
        guarantees is present.

    Raises:
        TokenExchangeFailedError: If the exchange fails, or succeeds
            without returning an access token.
    """
    try:
        token = await auth_flow.exchange_code(
            token_url=exchange.token_url,
            client_id=exchange.client_id,
            client_secret=exchange.client_secret,
            state=oauth_state,
            code=code,
            redirect_uri=oauth_state.redirect_uri,
        )
    except TokenExchangeFailedError:
        logger.warning(OAUTH_FLOW_FAILED, connection_name=conn.name)
        raise
    access_token = token.access_token
    if not access_token:
        logger.warning(
            OAUTH_FLOW_FAILED,
            connection_name=conn.name,
            reason="flow returned no access_token",
        )
        msg = "OAuth flow returned no access_token"
        raise TokenExchangeFailedError(msg)
    return _ExchangedTokens(token=token, access_token=access_token)


async def _persist_tokens(
    catalog: ConnectionCatalog,
    conn: Connection,
    exchanged: _ExchangedTokens,
) -> None:
    """Store the tokens through the secret backend and stamp their expiry."""
    token = exchanged.token
    rotated = await catalog.store_oauth_tokens(
        conn.name,
        access_token=exchanged.access_token,
        refresh_token=token.refresh_token,
    )
    # Seed from the row the rotation just re-read, not from the snapshot
    # taken before the exchange: the IdP round-trip sits between the two,
    # and the update below replaces the whole mapping, so anything written
    # to metadata in that window would be silently rolled back.
    meta_updates = dict(rotated.metadata)
    if token.expires_at:
        meta_updates["token_expires_at"] = token.expires_at.isoformat()
    else:
        # A non-expiring grant must also clear any stale stamp carried over
        # from a prior flow, or the token reads as long expired.
        meta_updates.pop("token_expires_at", None)
    await catalog.update(conn.name, metadata=meta_updates)


async def _consume_state(
    state_service: OAuthStateService,
    *,
    state_param: str,
    conn: Connection,
    clock: Clock,
) -> None:
    """Stamp the state consumed, AFTER the tokens are safely stored.

    A redelivered callback then sees ``consumed_at`` and returns the
    original connection name through the replay branch rather than
    re-exchanging the single-use authorization code. ``mark_consumed`` is
    the compare-and-set boundary: ``False`` means a concurrent callback
    already stamped the row, which the replay branch cannot catch for
    genuinely simultaneous flights, so it is surfaced rather than ignored.
    """
    if await state_service.mark_consumed(
        NotBlankStr(state_param),
        connection_name=NotBlankStr(conn.name),
        consumed_at=clock.now(),
    ):
        logger.info(OAUTH_FLOW_COMPLETED, connection_name=conn.name)
        return
    logger.warning(
        OAUTH_FLOW_COMPLETED,
        connection_name=conn.name,
        note="mark_consumed CAS lost; concurrent callback already stamped state",
    )
