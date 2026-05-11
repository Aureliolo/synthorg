"""Tunnel provider protocol."""

from typing import Protocol, runtime_checkable


# audit-keep 2026-05-10: multi-consumer wiring via IntegrationsBundle
# (state, state_services, integrations_wiring, mcp_service); vendor-agnostic
# tunnel abstraction (ngrok, cloudflared, etc.).
@runtime_checkable
class TunnelProvider(Protocol):
    """Public URL tunnel for webhook reception during local dev.

    Wraps a tunneling service (ngrok, cloudflared, etc.) to expose
    the local API server on a public URL.
    """

    async def start(self) -> str:
        """Start the tunnel.

        Returns:
            The public URL.

        Raises:
            TunnelError: If the tunnel cannot be started.
        """
        ...

    async def stop(self) -> None:
        """Stop and clean up the tunnel."""
        ...

    async def get_url(self) -> str | None:
        """Return the current public URL, or ``None`` if not running."""
        ...
