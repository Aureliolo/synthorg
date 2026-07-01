# module-kind: code
"""Tunnel manager factory.

Builds the :class:`TunnelManager` with one adapter per supported
provider. The local port is the API's own resolved serving port
(``api.server_port``), injected by the wiring layer so every provider
tunnels the address the server actually listens on.
"""

from synthorg.integrations.config import TunnelConfig
from synthorg.integrations.tunnel.cloudflare_adapter import (
    CloudflareQuickTunnelAdapter,
)
from synthorg.integrations.tunnel.devtunnels_adapter import DevTunnelsAdapter
from synthorg.integrations.tunnel.manager import TunnelManager
from synthorg.integrations.tunnel.ngrok_adapter import NgrokAdapter


def build_tunnel_manager(config: TunnelConfig, *, port: int) -> TunnelManager:
    """Assemble the multi-provider tunnel manager.

    Args:
        config: Static tunnel config (env-fallback token var, binary
            download policy).
        port: The local API port every adapter exposes.

    Returns:
        The manager, with Cloudflare quick tunnel as the default
        provider (accountless).
    """
    return TunnelManager(
        adapters=(
            CloudflareQuickTunnelAdapter(
                port=port,
                download_enabled=config.cloudflared_download_enabled,
            ),
            NgrokAdapter(auth_token_env=config.auth_token_env, port=port),
            DevTunnelsAdapter(port=port),
        ),
    )
