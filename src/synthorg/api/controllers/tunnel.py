"""Tunnel API controller.

Start/stop the local webhook tunnel for development.
"""

from litestar import Controller, get, post
from litestar.datastructures import State  # noqa: TC002

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.integrations.errors import TunnelError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    TUNNEL_ERROR,
    TUNNEL_STARTED,
    TUNNEL_STOPPED,
)

logger = get_logger(__name__)


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
        """Start the ngrok tunnel and return the public URL."""
        tunnel = state["app_state"].tunnel_provider
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
            # URLs / auth-token fragments. Surface a stable generic
            # message to the client; details land in the scrubbed
            # log above.
            client_msg = "Tunnel service is unavailable"
            raise ServiceUnavailableError(client_msg) from exc
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
        """Stop the ngrok tunnel."""
        tunnel = state["app_state"].tunnel_provider
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
            client_msg = "Tunnel service is unavailable"
            raise ServiceUnavailableError(client_msg) from exc
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
        """
        tunnel = state["app_state"].tunnel_provider
        url = await tunnel.get_url()
        return ApiResponse(
            data={
                "public_url": url,
                "has_auth_token": tunnel.has_auth_token,
            },
        )
