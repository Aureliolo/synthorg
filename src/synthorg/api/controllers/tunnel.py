"""Tunnel API controller.

Start/stop the local webhook tunnel for development.
"""

from litestar import Controller, get, post
from litestar.datastructures import State  # noqa: TC002

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.integrations.errors import TunnelError
from synthorg.observability import get_logger
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
        from synthorg.core.domain_errors import ServiceUnavailableError  # noqa: PLC0415

        tunnel = state["app_state"].tunnel_provider
        try:
            url = await tunnel.start()
        except TunnelError as exc:
            logger.warning(
                TUNNEL_ERROR,
                action="start",
                error=str(exc),
            )
            raise ServiceUnavailableError(str(exc)) from exc
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
        from synthorg.core.domain_errors import ServiceUnavailableError  # noqa: PLC0415

        tunnel = state["app_state"].tunnel_provider
        try:
            await tunnel.stop()
        except TunnelError as exc:
            logger.warning(
                TUNNEL_ERROR,
                action="stop",
                error=str(exc),
            )
            raise ServiceUnavailableError(str(exc)) from exc
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
    ) -> ApiResponse[dict[str, str | None]]:
        """Get the current tunnel URL or None if stopped."""
        tunnel = state["app_state"].tunnel_provider
        url = await tunnel.get_url()
        return ApiResponse(data={"public_url": url})
