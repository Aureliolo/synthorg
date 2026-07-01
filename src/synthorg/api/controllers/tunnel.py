"""Tunnel API controller.

Start/stop the local webhook tunnel for development.
"""

from typing import Final

from litestar import Controller, get, post
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.integrations.errors import TunnelError
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    TUNNEL_ERROR,
    TUNNEL_STARTED,
    TUNNEL_STOPPED,
)

logger = get_logger(__name__)

_MISSING_AUTH_MESSAGE: Final[str] = (
    "No ngrok auth token configured. ngrok requires an account and auth token "
    "to start a tunnel; set NGROK_AUTHTOKEN on the backend, then retry."
)
_UNAVAILABLE_MESSAGE: Final[str] = "Tunnel service is unavailable"


class TunnelController(Controller):
    """Start/stop webhook tunnel for local development."""

    path = "/integrations/tunnel"
    tags = ["Integrations"]  # noqa: RUF012

    @post(
        "/start",
        guards=[require_write_access],
        summary="Start webhook tunnel",
    )
    async def start_tunnel(
        self,
        state: State,
    ) -> ApiResponse[dict[str, str]]:
        """Start the ngrok tunnel and return the public URL.

        Returns:
            ``ApiResponse[dict[str, str]]`` instance.

        Raises:
            ServiceUnavailableError: Raised on the corresponding failure path.
        """
        tunnel = require_service(
            state["app_state"].slice(IntegrationsStateSlice).tunnel_provider,
            "Tunnel Provider",
        )
        # Fail fast on the guaranteed-doomed case: ngrok refuses every
        # session without an auth token (ERR_NGROK_4018), so spawning
        # the agent would only download the binary, storm the log with
        # critical-level ngrok errors, and return the same failure.
        # ``has_auth_token`` is our own config state, so surfacing it
        # here leaks no secret.
        if not tunnel.has_auth_token:
            raise ServiceUnavailableError(_MISSING_AUTH_MESSAGE)
        try:
            url = await tunnel.start()
        except TunnelError as exc:
            logger.warning(
                TUNNEL_ERROR,
                action="start",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            # The tunnel provider's exception text can carry ngrok
            # URLs / auth-token fragments, so it is never echoed to the
            # client: surface a stable generic message and let the
            # scrubbed log above carry the detail.
            raise ServiceUnavailableError(_UNAVAILABLE_MESSAGE) from exc
        logger.info(
            TUNNEL_STARTED,
            public_url=url,
        )
        return ApiResponse(data={"public_url": url})

    @post(
        "/stop",
        guards=[require_write_access],
        summary="Stop webhook tunnel",
    )
    async def stop_tunnel(
        self,
        state: State,
    ) -> ApiResponse[None]:
        """Stop the ngrok tunnel.

        Returns:
            ``ApiResponse[None]`` instance.

        Raises:
            ServiceUnavailableError: Raised on the corresponding failure path.
        """
        tunnel = require_service(
            state["app_state"].slice(IntegrationsStateSlice).tunnel_provider,
            "Tunnel Provider",
        )
        try:
            await tunnel.stop()
        except TunnelError as exc:
            logger.warning(
                TUNNEL_ERROR,
                action="stop",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            # Same client-facing redaction as ``start``.
            raise ServiceUnavailableError(_UNAVAILABLE_MESSAGE) from exc
        logger.info(TUNNEL_STOPPED)
        return ApiResponse(data=None)

    @get(
        "/status",
        guards=[require_read_access],
        summary="Get tunnel status",
    )
    async def get_status(
        self,
        state: State,
    ) -> ApiResponse[dict[str, str | bool | None]]:
        """Get the current tunnel URL plus credential presence.

        ``has_auth_token`` lets the dashboard surface a free-tier
        notice and a link to configure NGROK_AUTHTOKEN before the
        operator hits the limits.

        Returns:
            The configured value when present, ``None`` otherwise.
        """
        tunnel = require_service(
            state["app_state"].slice(IntegrationsStateSlice).tunnel_provider,
            "Tunnel Provider",
        )
        url = await tunnel.get_url()
        return ApiResponse(
            data={
                "public_url": url,
                "has_auth_token": tunnel.has_auth_token,
            },
        )
