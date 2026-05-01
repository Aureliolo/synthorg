"""OAuth 2.1 authorization code flow with PKCE."""

import json
import math
import secrets as stdlib_secrets
from datetime import timedelta
from urllib.parse import urlencode

import httpx

from synthorg.api.auth.token_size import get_auth_token_bytes
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import (
    OAuthState,
    OAuthToken,
)
from synthorg.integrations.errors import (
    TokenExchangeFailedError,
    TokenRefreshFailedError,
)
from synthorg.integrations.oauth.pkce import (
    decrypt_pkce_verifier,
    encrypt_pkce_verifier,
    generate_code_challenge,
    generate_code_verifier,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    OAUTH_FLOW_STARTED,
    OAUTH_TOKEN_EXCHANGE_FAILED,
    OAUTH_TOKEN_EXCHANGED,
    OAUTH_TOKEN_REFRESH_FAILED,
    OAUTH_TOKEN_REFRESHED,
)

logger = get_logger(__name__)

_DEFAULT_HTTP_TIMEOUT_SECONDS: float = 30.0
"""Fallback OAuth HTTP timeout used when no operator override is supplied."""


class AuthorizationCodeFlow:
    """OAuth 2.1 authorization code flow with PKCE.

    Implements the three-step flow: start (build auth URL),
    exchange (code for tokens), and refresh.

    Args:
        http_timeout_seconds: HTTP timeout for token exchange + refresh
            calls (mirrors ``integrations.oauth_http_timeout_seconds``).
        clock: Time source. Defaults to ``SystemClock``; tests inject
            ``FakeClock`` from ``tests/_shared/fake_clock.py`` so
            ``OAuthState`` and ``OAuthToken`` ``expires_at`` are
            deterministic without real time.
    """

    def __init__(
        self,
        *,
        http_timeout_seconds: float = _DEFAULT_HTTP_TIMEOUT_SECONDS,
        clock: Clock | None = None,
    ) -> None:
        # Strict numeric + finite + positive: reject ``bool`` (which
        # is an ``int`` subclass and would silently flow into
        # ``httpx.AsyncClient(timeout=True)`` -> 1 s) and reject
        # ``NaN`` / ``+Inf`` / ``-Inf`` so they cannot reach ``httpx``
        # where their behaviour is implementation-defined.
        if (
            isinstance(http_timeout_seconds, bool)
            or not isinstance(http_timeout_seconds, (int, float))
            or not math.isfinite(float(http_timeout_seconds))
            or float(http_timeout_seconds) <= 0.0
        ):
            msg = (
                "http_timeout_seconds must be a finite positive number,"
                f" got {http_timeout_seconds!r}"
                f" of type {type(http_timeout_seconds).__name__}"
            )
            raise ValueError(msg)
        self._http_timeout_seconds = float(http_timeout_seconds)
        self._clock: Clock = clock if clock is not None else SystemClock()

    @property
    def grant_type(self) -> str:
        """OAuth grant type identifier."""
        return "authorization_code"

    @property
    def supports_refresh(self) -> bool:
        """Whether this flow produces refresh tokens."""
        return True

    async def start_flow(  # noqa: PLR0913
        self,
        *,
        auth_url: str,
        token_url: str,  # noqa: ARG002
        client_id: str,
        client_secret: str,  # noqa: ARG002
        scopes: tuple[str, ...],
        redirect_uri: str,
    ) -> tuple[str, OAuthState]:
        """Build the authorization URL and create state.

        Returns:
            (authorization_url, OAuthState) tuple.
        """
        state_token = stdlib_secrets.token_urlsafe(get_auth_token_bytes())
        verifier = generate_code_verifier()
        challenge = generate_code_challenge(verifier)
        # The verifier is persisted until the code exchange completes;
        # encrypt it at rest so a DB leak alone is not enough to
        # complete an intercepted authorization code.
        encrypted_verifier = encrypt_pkce_verifier(verifier)

        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state_token,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        authorization_url = f"{auth_url}?{urlencode(params)}"

        now = self._clock.now()
        oauth_state = OAuthState(
            state_token=NotBlankStr(state_token),
            connection_name=NotBlankStr("pending"),
            pkce_verifier=NotBlankStr(encrypted_verifier),
            scopes_requested=" ".join(scopes),
            redirect_uri=redirect_uri,
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        logger.info(OAUTH_FLOW_STARTED, grant_type="authorization_code")
        return authorization_url, oauth_state

    async def exchange_code(  # noqa: PLR0913
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        state: OAuthState,
        code: str,
        redirect_uri: str,
    ) -> OAuthToken:
        """Exchange authorization code for tokens.

        Raises:
            TokenExchangeFailedError: If the exchange fails.
        """
        if state.pkce_verifier is None:
            msg = "PKCE verifier missing from OAuth state"
            raise TokenExchangeFailedError(msg)
        # state.pkce_verifier is encrypted at rest; decrypt before
        # sending to the OAuth provider.
        try:
            verifier = decrypt_pkce_verifier(state.pkce_verifier)
        except Exception as exc:
            logger.warning(
                OAUTH_TOKEN_EXCHANGE_FAILED,
                error="failed to decrypt stored PKCE verifier",
            )
            msg = f"Invalid PKCE verifier in OAuth state: {exc}"
            raise TokenExchangeFailedError(msg) from exc

        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": verifier,
        }
        try:
            async with httpx.AsyncClient(timeout=self._http_timeout_seconds) as client:
                resp = await client.post(token_url, data=payload)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            # Use ``warning`` (no traceback) + scrubbed description so the
            # request payload that contained ``client_secret`` and
            # ``code_verifier`` cannot leak through frame-local repr's or
            # stringified error bodies.
            logger.warning(
                OAUTH_TOKEN_EXCHANGE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Token exchange failed: {type(exc).__name__}"
            raise TokenExchangeFailedError(msg) from exc

        return self._parse_token_response(data, "exchange")

    async def refresh_token(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> OAuthToken:
        """Refresh an access token.

        Raises:
            TokenRefreshFailedError: If the refresh fails or the
                response cannot be parsed.
        """
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        try:
            async with httpx.AsyncClient(timeout=self._http_timeout_seconds) as client:
                resp = await client.post(token_url, data=payload)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            # See ``exchange_code`` above: ``warning`` + scrubbed description
            # keeps the refresh_token and client_secret out of logs.
            logger.warning(
                OAUTH_TOKEN_REFRESH_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Token refresh failed: {type(exc).__name__}"
            raise TokenRefreshFailedError(msg) from exc

        try:
            return self._parse_token_response(data, "refresh")
        except TokenExchangeFailedError as exc:
            # Normalize refresh failures to the refresh error domain.
            # Parse-error messages are already sanitized (we build them
            # ourselves), but scrub defensively for consistency.
            logger.warning(
                OAUTH_TOKEN_REFRESH_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise TokenRefreshFailedError(safe_error_description(exc)) from exc

    def _parse_token_response(  # noqa: C901, PLR0912
        self,
        data: object,
        operation: str,
    ) -> OAuthToken:
        """Parse a token endpoint response into an OAuthToken.

        Accepts ``object`` because ``resp.json()`` can return a list
        or scalar as well as a dict, and we must reject non-dict
        payloads before reading fields.

        Returns an ``OAuthToken`` with raw ``access_token`` /
        ``refresh_token`` populated. The caller is responsible
        for persisting them via
        ``ConnectionCatalog.store_oauth_tokens``.
        """
        if not isinstance(data, dict):
            msg = (
                f"Token {operation} response is not a JSON object: "
                f"{type(data).__name__}"
            )
            raise TokenExchangeFailedError(msg)
        access_token_raw = data.get("access_token")
        if not isinstance(access_token_raw, str) or not access_token_raw:
            msg = f"No valid access_token in {operation} response"
            raise TokenExchangeFailedError(msg)
        access_token = access_token_raw

        expires_in = data.get("expires_in")
        expires_at = None
        if isinstance(expires_in, int) and expires_in > 0:
            expires_at = self._clock.now() + timedelta(seconds=expires_in)

        # Fail fast on malformed optional fields instead of silently
        # coercing or defaulting them. A token endpoint that sends
        # ``{"refresh_token": 123}``, ``{"token_type": 123}``, or
        # ``{"scope": ["a", "b"]}`` is violating the protocol and
        # the caller needs to know, not see a string-coerced value.
        if "refresh_token" in data:
            refresh_value = data.get("refresh_token")
            if refresh_value is None:
                refresh_raw: str | None = None
            elif isinstance(refresh_value, str):
                refresh_raw = refresh_value or None
            else:
                msg = (
                    f"Token {operation} response has non-string "
                    f"refresh_token: {type(refresh_value).__name__}"
                )
                raise TokenExchangeFailedError(msg)
        else:
            refresh_raw = None

        if "token_type" in data:
            token_type_value = data.get("token_type")
            if not isinstance(token_type_value, str):
                msg = (
                    f"Token {operation} response has non-string "
                    f"token_type: {type(token_type_value).__name__}"
                )
                raise TokenExchangeFailedError(msg)
            token_type = token_type_value
        else:
            token_type = "Bearer"  # noqa: S105

        if "scope" in data:
            scope_value = data.get("scope")
            if not isinstance(scope_value, str):
                msg = (
                    f"Token {operation} response has non-string "
                    f"scope: {type(scope_value).__name__}"
                )
                raise TokenExchangeFailedError(msg)
            scope = scope_value
        else:
            scope = ""

        event = (
            OAUTH_TOKEN_EXCHANGED if operation == "exchange" else OAUTH_TOKEN_REFRESHED
        )
        logger.info(event, has_refresh=refresh_raw is not None)

        return OAuthToken(
            access_token=access_token,
            refresh_token=refresh_raw,
            token_type=token_type,
            expires_at=expires_at,
            scope_granted=scope,
        )
