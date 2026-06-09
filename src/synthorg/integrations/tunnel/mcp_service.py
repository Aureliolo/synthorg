"""TunnelService -- MCP facade over :class:`TunnelProvider`.

Maps the two MCP tunnel tools onto the provider's minimal lifecycle
contract (``start`` / ``stop`` / ``get_url``):

* ``synthorg_tunnel_get_status`` -- current URL + running state.
* ``synthorg_tunnel_connect`` -- idempotent ``start()`` returning URL.
"""

from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.tunnel.protocol import TunnelProvider
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.communication import (
    COMMUNICATION_TUNNEL_CONNECTED,
    COMMUNICATION_TUNNEL_PROVIDER_ERROR,
    COMMUNICATION_TUNNEL_STATUS_CHECKED,
)

logger = get_logger(__name__)


class TunnelStatus:
    """Lightweight status snapshot for the tunnel.

    Attributes:
        running: Whether the tunnel is currently active.
        url: Public URL when running; ``None`` otherwise.
    """

    __slots__ = ("running", "url")

    def __init__(self, *, running: bool, url: str | None) -> None:
        self.running = running
        self.url = url

    def to_dict(self) -> dict[str, object]:
        """Serialise to a JSON-safe dict.

        Returns:
            A dict with ``running`` and ``url`` keys.
        """
        return {"running": self.running, "url": self.url}


class TunnelService:
    """Facade wrapping :class:`TunnelProvider` for the MCP surface."""

    def __init__(self, *, provider: TunnelProvider) -> None:
        self._provider = provider

    async def get_status(self) -> TunnelStatus:
        """Return the tunnel's running state + URL."""
        try:
            url = await self._provider.get_url()
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                COMMUNICATION_TUNNEL_PROVIDER_ERROR,
                method="get_url",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        status = TunnelStatus(running=url is not None, url=url)
        logger.info(
            COMMUNICATION_TUNNEL_STATUS_CHECKED,
            running=status.running,
        )
        return status

    async def connect(self) -> TunnelStatus:
        """Start (or return status for) the tunnel.

        Calling ``start()`` on an already-running provider should be
        idempotent per the protocol contract; this facade surfaces a
        uniform :class:`TunnelStatus` either way.

        Returns:
            A ``TunnelStatus`` with ``running=True`` and the public URL
            returned by the provider's ``start()`` call.
        """
        try:
            url = await self._provider.start()
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                COMMUNICATION_TUNNEL_PROVIDER_ERROR,
                method="start",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.info(
            COMMUNICATION_TUNNEL_CONNECTED,
            url=url,
        )
        return TunnelStatus(running=True, url=url)


__all__ = [
    "TunnelService",
    "TunnelStatus",
]
