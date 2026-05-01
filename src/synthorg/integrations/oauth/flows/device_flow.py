"""OAuth 2.1 device authorization flow (RFC 8628)."""

import json
from datetime import timedelta

import httpx

from synthorg.core.clock import Clock, SystemClock
from synthorg.integrations.connections.models import OAuthToken
from synthorg.integrations.errors import (
    DeviceFlowTimeoutError,
    TokenExchangeFailedError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    OAUTH_DEVICE_FLOW_GRANTED,
    OAUTH_DEVICE_FLOW_POLLING,
    OAUTH_DEVICE_FLOW_STARTED,
    OAUTH_DEVICE_FLOW_TIMEOUT,
    OAUTH_TOKEN_EXCHANGE_FAILED,
)

logger = get_logger(__name__)


class DeviceFlowResult:
    """Result of initiating a device flow.

    Attributes:
        device_code: The device code for polling.
        user_code: The code the user enters at the verification URL.
        verification_uri: URL where the user authorizes.
        verification_uri_complete: Pre-filled URL (if available).
        interval: Polling interval in seconds.
        expires_in: Seconds until the device code expires.
    """

    __slots__ = (
        "device_code",
        "expires_in",
        "interval",
        "user_code",
        "verification_uri",
        "verification_uri_complete",
    )

    def __init__(  # noqa: PLR0913
        self,
        *,
        device_code: str,
        user_code: str,
        verification_uri: str,
        verification_uri_complete: str = "",
        interval: int,
        expires_in: int = 600,
    ) -> None:
        self.device_code = device_code
        self.user_code = user_code
        self.verification_uri = verification_uri
        self.verification_uri_complete = verification_uri_complete
        self.interval = interval
        self.expires_in = expires_in


_DEFAULT_HTTP_TIMEOUT_SECONDS: float = 30.0
"""Fallback OAuth HTTP timeout used when no operator override is supplied."""

_DEFAULT_POLL_INTERVAL_SECONDS: int = 5
"""RFC 8628 baseline polling cadence (5 s) used when the server omits
``interval`` from the device-authorization response. Operators tune this
through the ``integrations.oauth_device_flow_poll_interval_seconds``
setting; resolve at construction and pass into ``DeviceFlow``."""


class DeviceFlow:
    """OAuth 2.1 device authorization flow (RFC 8628).

    Designed for CLI/headless use where the user cannot interact
    with a browser redirect.  The user enters a code at a URL
    displayed by the application.

    Args:
        http_timeout_seconds: HTTP timeout for initiate + poll token
            calls (mirrors ``integrations.oauth_http_timeout_seconds``).
        default_poll_interval_seconds: Fallback polling cadence used
            when the device-authorization response omits ``interval``.
            Mirrors the
            ``integrations.oauth_device_flow_poll_interval_seconds``
            setting; resolve at the construction site and pass through.
            The IETF dynamic ``slow_down`` widening (``+5`` per response)
            in ``poll_for_token`` is preserved.
        clock: Time / cooperative-sleep source. Defaults to
            ``SystemClock``; tests inject ``FakeClock`` from
            ``tests/_shared/fake_clock.py`` to make the polling loop
            deterministic without real waits.
    """

    def __init__(
        self,
        *,
        http_timeout_seconds: float = _DEFAULT_HTTP_TIMEOUT_SECONDS,
        default_poll_interval_seconds: int = _DEFAULT_POLL_INTERVAL_SECONDS,
        clock: Clock | None = None,
    ) -> None:
        if http_timeout_seconds <= 0:
            msg = f"http_timeout_seconds must be > 0, got {http_timeout_seconds}"
            raise ValueError(msg)
        # Strictly positive ``int`` only.  Reject ``bool`` (which is an
        # ``int`` subclass: ``True == 1`` and ``False == 0`` would pass
        # the comparison silently) and reject ``float`` because the
        # response-parser default uses ``_positive_int`` which already
        # rejects floats; an inconsistent fallback would shadow that
        # boundary check.
        if (
            not isinstance(default_poll_interval_seconds, int)
            or isinstance(default_poll_interval_seconds, bool)
            or default_poll_interval_seconds <= 0
        ):
            msg = (
                "default_poll_interval_seconds must be a positive int"
                f" (not bool/float), got {default_poll_interval_seconds!r}"
                f" of type {type(default_poll_interval_seconds).__name__}"
            )
            raise ValueError(msg)
        self._http_timeout_seconds = http_timeout_seconds
        self._default_poll_interval_seconds = default_poll_interval_seconds
        self._clock: Clock = clock if clock is not None else SystemClock()

    @property
    def grant_type(self) -> str:
        """OAuth grant type identifier."""
        return "urn:ietf:params:oauth:grant-type:device_code"

    @property
    def supports_refresh(self) -> bool:
        """Whether this flow produces refresh tokens."""
        return True

    async def request_device_code(
        self,
        *,
        device_authorization_url: str,
        client_id: str,
        scopes: tuple[str, ...] = (),
    ) -> DeviceFlowResult:
        """Request a device code from the authorization server.

        Args:
            device_authorization_url: The device authorization endpoint.
            client_id: OAuth client ID.
            scopes: Requested scopes.

        Returns:
            A ``DeviceFlowResult`` with user code and verification URL.

        Raises:
            TokenExchangeFailedError: If the request fails.
        """
        payload: dict[str, str] = {"client_id": client_id}
        if scopes:
            payload["scope"] = " ".join(scopes)

        try:
            async with httpx.AsyncClient(timeout=self._http_timeout_seconds) as client:
                resp = await client.post(
                    device_authorization_url,
                    data=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            # Scrubbed + no traceback: the POST body may carry
            # ``client_id`` / scopes and some providers echo it back
            # in error responses.
            logger.warning(
                OAUTH_TOKEN_EXCHANGE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Device code request failed: {type(exc).__name__}"
            raise TokenExchangeFailedError(msg) from exc

        # Validate the response shape before indexing / coercing:
        # ``resp.json()`` can return a list or scalar which would
        # otherwise blow up with ``AttributeError`` or ``KeyError``
        # on the next line, bypassing the flow's error contract.
        if not isinstance(data, dict):
            logger.warning(
                OAUTH_TOKEN_EXCHANGE_FAILED,
                error="device code response is not a JSON object",
                response_type=type(data).__name__,
            )
            msg = f"Device code response is not a JSON object: {type(data).__name__}"
            raise TokenExchangeFailedError(msg)
        required = ("device_code", "user_code", "verification_uri")
        missing = [
            key
            for key in required
            if not isinstance(data.get(key), str) or not data.get(key)
        ]
        if missing:
            logger.warning(
                OAUTH_TOKEN_EXCHANGE_FAILED,
                error="device code response missing required fields",
                missing=missing,
            )
            msg = f"Device code response missing required fields: {missing}"
            raise TokenExchangeFailedError(msg)

        # Validate numeric fields strictly: plain ``int(...)`` would
        # quietly accept negatives, zero, and string floats like
        # ``"5.5"``. The polling loop needs a strictly-positive
        # integer ``interval`` and a strictly-positive integer
        # ``expires_in``.
        def _positive_int(field_name: str, default: int) -> int:
            raw: object = data.get(field_name, default)
            if isinstance(raw, bool) or not isinstance(raw, int):
                msg = (
                    f"Device code response '{field_name}' must be "
                    f"a positive integer (got {type(raw).__name__})"
                )
                logger.warning(
                    OAUTH_TOKEN_EXCHANGE_FAILED,
                    error=msg,
                )
                raise TokenExchangeFailedError(msg)
            if raw <= 0:
                msg = (
                    f"Device code response '{field_name}' must be "
                    f"strictly positive (got {raw})"
                )
                logger.warning(
                    OAUTH_TOKEN_EXCHANGE_FAILED,
                    error=msg,
                )
                raise TokenExchangeFailedError(msg)
            return raw

        interval_value = _positive_int("interval", self._default_poll_interval_seconds)
        expires_in_value = _positive_int("expires_in", 600)

        # user_code is an active credential -- do not log it at
        # INFO. Only the verification URI is safe to surface.
        logger.info(
            OAUTH_DEVICE_FLOW_STARTED,
            verification_uri=data.get("verification_uri"),
        )
        return DeviceFlowResult(
            device_code=str(data["device_code"]),
            user_code=str(data["user_code"]),
            verification_uri=str(data["verification_uri"]),
            verification_uri_complete=str(
                data.get("verification_uri_complete", ""),
            ),
            interval=interval_value,
            expires_in=expires_in_value,
        )

    async def poll_for_token(  # noqa: C901, PLR0912, PLR0915
        self,
        *,
        token_url: str,
        client_id: str,
        device_code: str,
        interval: int,
        max_wait_seconds: int = 600,
    ) -> OAuthToken:
        """Poll the token endpoint until the user authorizes.

        Args:
            token_url: Token endpoint URL.
            client_id: OAuth client ID.
            device_code: Device code from ``request_device_code``.
            interval: Polling interval in seconds.
            max_wait_seconds: Max seconds to wait.

        Returns:
            The granted ``OAuthToken``.

        Raises:
            DeviceFlowTimeoutError: If the user does not authorize
                within the timeout.
            TokenExchangeFailedError: On unexpected errors.
            ValueError: If ``interval`` or ``max_wait_seconds`` is
                non-positive (would cause a tight loop / immediate
                timeout).
        """
        if interval <= 0:
            msg = f"interval must be > 0, got {interval}"
            raise ValueError(msg)
        if max_wait_seconds <= 0:
            msg = f"max_wait_seconds must be > 0, got {max_wait_seconds}"
            raise ValueError(msg)
        payload = {
            "grant_type": self.grant_type,
            "client_id": client_id,
            "device_code": device_code,
        }
        # Use the monotonic clock for the polling deadline + remaining-
        # budget math: a wall-clock jump (NTP correction, manual time
        # change, container clock skew) would otherwise either
        # short-circuit the loop early or extend it past the caller's
        # ``max_wait_seconds`` budget.  ``self._clock.now()`` (UTC
        # wall-clock) is still used downstream for ``token.expires_at``
        # because that field is a persisted absolute timestamp the
        # operator inspects.
        monotonic_deadline = self._clock.monotonic() + max_wait_seconds
        poll_interval = interval

        while self._clock.monotonic() < monotonic_deadline:
            # Clamp the sleep to the remaining budget so the loop never
            # overshoots the deadline by up to ``poll_interval`` seconds
            # (and never makes one extra token-endpoint POST after the
            # caller's max_wait_seconds is exhausted).
            remaining = monotonic_deadline - self._clock.monotonic()
            if remaining <= 0:
                break
            sleep_seconds = min(poll_interval, remaining)
            logger.debug(OAUTH_DEVICE_FLOW_POLLING, interval=sleep_seconds)
            await self._clock.sleep(sleep_seconds)
            # Re-check after waking: ``FakeClock.sleep`` and a real
            # ``asyncio.sleep`` both advance time to (or just past)
            # the deadline, and the surrounding ``while`` only re-runs
            # on the next iteration. Without this break the current
            # iteration still issues the token-endpoint POST after the
            # caller's budget has expired.
            if self._clock.monotonic() >= monotonic_deadline:
                break

            try:
                async with httpx.AsyncClient(
                    timeout=self._http_timeout_seconds
                ) as client:
                    resp = await client.post(token_url, data=payload)
                    status_code = resp.status_code
                    data = resp.json()
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                # The polling POST body carries the ``device_code``
                # which is a credential until the user authorizes.
                logger.warning(
                    OAUTH_TOKEN_EXCHANGE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                msg = f"Device flow polling failed: {type(exc).__name__}"
                raise TokenExchangeFailedError(msg) from exc

            if not isinstance(data, dict):
                logger.warning(
                    OAUTH_TOKEN_EXCHANGE_FAILED,
                    error="device token response is not a JSON object",
                    status_code=status_code,
                    response_type=type(data).__name__,
                )
                msg = (
                    f"Device token response is not a JSON object: {type(data).__name__}"
                )
                raise TokenExchangeFailedError(msg)

            error = data.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                poll_interval += 5
                continue
            if error == "expired_token":
                break
            if error:
                msg = f"Device flow error: {error}"
                raise TokenExchangeFailedError(msg)

            access_token_raw = data.get("access_token")
            if access_token_raw is not None and access_token_raw != "":
                # Enforce types explicitly rather than blindly
                # coercing with ``str()``. A malformed response that
                # returns e.g. ``{"access_token": 123}`` should fail
                # fast so callers get a clear protocol error.
                if not isinstance(access_token_raw, str):
                    msg = (
                        "Device flow token response has non-string "
                        f"access_token: {type(access_token_raw).__name__}"
                    )
                    logger.warning(
                        OAUTH_TOKEN_EXCHANGE_FAILED,
                        error=msg,
                    )
                    raise TokenExchangeFailedError(msg)
                logger.info(OAUTH_DEVICE_FLOW_GRANTED)
                expires_in = data.get("expires_in")
                expires_at = None
                if (
                    isinstance(expires_in, int)
                    and not isinstance(
                        expires_in,
                        bool,
                    )
                    and expires_in > 0
                ):
                    expires_at = self._clock.now() + timedelta(
                        seconds=expires_in,
                    )
                refresh_raw = data.get("refresh_token")
                if refresh_raw is None or refresh_raw == "":
                    refresh_value: str | None = None
                elif isinstance(refresh_raw, str):
                    refresh_value = refresh_raw
                else:
                    msg = (
                        "Device flow token response has non-string "
                        f"refresh_token: {type(refresh_raw).__name__}"
                    )
                    logger.warning(
                        OAUTH_TOKEN_EXCHANGE_FAILED,
                        error=msg,
                    )
                    raise TokenExchangeFailedError(msg)
                token_type_raw = data.get("token_type", "Bearer")
                if not isinstance(token_type_raw, str):
                    msg = (
                        "Device flow token response has non-string "
                        f"token_type: {type(token_type_raw).__name__}"
                    )
                    logger.warning(
                        OAUTH_TOKEN_EXCHANGE_FAILED,
                        error=msg,
                    )
                    raise TokenExchangeFailedError(msg)
                scope_raw = data.get("scope", "")
                if not isinstance(scope_raw, str):
                    msg = (
                        "Device flow token response has non-string "
                        f"scope: {type(scope_raw).__name__}"
                    )
                    logger.warning(
                        OAUTH_TOKEN_EXCHANGE_FAILED,
                        error=msg,
                    )
                    raise TokenExchangeFailedError(msg)
                return OAuthToken(
                    access_token=access_token_raw,
                    refresh_token=refresh_value,
                    token_type=token_type_raw,
                    expires_at=expires_at,
                    scope_granted=scope_raw,
                )
            # Fail fast: a non-success status with no recognized
            # RFC 8628 error code means the authorization server
            # returned an unexpected shape. Keep polling until the
            # deadline would silently paper over the problem.
            if status_code >= 400:  # noqa: PLR2004
                logger.warning(
                    OAUTH_TOKEN_EXCHANGE_FAILED,
                    error="device token endpoint returned unexpected error",
                    status_code=status_code,
                )
                msg = (
                    "Device flow token endpoint returned "
                    f"HTTP {status_code} with no RFC 8628 error field"
                )
                raise TokenExchangeFailedError(msg)
            logger.warning(
                OAUTH_TOKEN_EXCHANGE_FAILED,
                error="device token endpoint returned unexpected shape",
                status_code=status_code,
            )
            msg = (
                "Device flow token endpoint returned an unexpected "
                "response with neither error nor access_token"
            )
            raise TokenExchangeFailedError(msg)

        logger.warning(
            OAUTH_DEVICE_FLOW_TIMEOUT,
            max_wait_seconds=max_wait_seconds,
        )
        msg = f"Device flow timed out after {max_wait_seconds}s"
        raise DeviceFlowTimeoutError(msg)
