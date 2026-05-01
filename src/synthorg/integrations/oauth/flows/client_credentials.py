"""OAuth 2.1 client credentials flow (machine-to-machine)."""

import json
import math
from datetime import timedelta

import httpx

from synthorg.core.clock import Clock, SystemClock
from synthorg.integrations.connections.models import OAuthToken
from synthorg.integrations.errors import TokenExchangeFailedError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    OAUTH_TOKEN_EXCHANGE_FAILED,
    OAUTH_TOKEN_EXCHANGED,
)

logger = get_logger(__name__)

_DEFAULT_HTTP_TIMEOUT_SECONDS: float = 30.0
"""Fallback OAuth HTTP timeout used when no operator override is supplied."""


class ClientCredentialsFlow:
    """OAuth 2.1 client credentials flow for M2M auth.

    No user interaction required.  The client authenticates
    directly with its own credentials.

    Args:
        http_timeout_seconds: HTTP timeout for the token exchange call.
            Defaults to :data:`_DEFAULT_HTTP_TIMEOUT_SECONDS`; callers
            are expected to pass the operator-tuned value from
            ``ConfigResolver.get_float('integrations',
            'oauth_http_timeout_seconds')`` at construction.
        clock: Time source. Defaults to ``SystemClock``; tests inject
            ``FakeClock`` from ``tests/_shared/fake_clock.py`` so
            ``OAuthToken.expires_at`` is deterministic without real
            time.
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
        return "client_credentials"

    @property
    def supports_refresh(self) -> bool:
        """Whether this flow produces refresh tokens."""
        return False

    async def exchange(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        scopes: tuple[str, ...] = (),
    ) -> OAuthToken:
        """Exchange client credentials for an access token.

        Args:
            token_url: Token endpoint URL.
            client_id: OAuth client ID.
            client_secret: OAuth client secret.
            scopes: Requested scopes.

        Returns:
            The granted ``OAuthToken`` (no refresh token).

        Raises:
            TokenExchangeFailedError: If the exchange fails.
        """
        payload: dict[str, str] = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if scopes:
            payload["scope"] = " ".join(scopes)

        try:
            async with httpx.AsyncClient(timeout=self._http_timeout_seconds) as client:
                resp = await client.post(token_url, data=payload)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            # ``warning`` + scrubbed description: the POST body carries
            # ``client_secret`` and some providers echo it into error
            # responses, so neither the traceback nor the stringified
            # exception is safe to log verbatim.
            logger.warning(
                OAUTH_TOKEN_EXCHANGE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Client credentials exchange failed: {type(exc).__name__}"
            raise TokenExchangeFailedError(msg) from exc

        if not isinstance(data, dict):
            logger.warning(
                OAUTH_TOKEN_EXCHANGE_FAILED,
                error="token response is not a JSON object",
                response_type=type(data).__name__,
            )
            msg = (
                "Client credentials response is not a JSON object: "
                f"{type(data).__name__}"
            )
            raise TokenExchangeFailedError(msg)
        access_token_raw = data.get("access_token")
        if not isinstance(access_token_raw, str) or not access_token_raw:
            logger.warning(
                OAUTH_TOKEN_EXCHANGE_FAILED,
                error="missing or invalid access_token",
            )
            msg = "No valid access_token in client credentials response"
            raise TokenExchangeFailedError(msg)
        access_token = access_token_raw

        expires_in = data.get("expires_in")
        expires_at = None
        if isinstance(expires_in, int) and expires_in > 0:
            expires_at = self._clock.now() + timedelta(seconds=expires_in)

        logger.info(OAUTH_TOKEN_EXCHANGED, grant_type="client_credentials")
        return OAuthToken(
            access_token=access_token,
            token_type=str(data.get("token_type", "Bearer")),
            expires_at=expires_at,
            scope_granted=str(data.get("scope", "")),
        )
