# module-kind: code
"""Tunnel manager factory.

Builds the :class:`TunnelManager` with one adapter per supported
provider. The local port is the API's own resolved serving port
(``api.server_port``), injected by the wiring layer so every provider
tunnels the address the server actually listens on. The state dir
roots all tunnel runtime state (downloaded binaries, the devtunnel
CLI's confined login home) so a container install can point it at
persistent storage.
"""

from pathlib import Path

from synthorg.integrations.config import TunnelConfig
from synthorg.integrations.tunnel._binaries import (
    default_binary_dir,
    default_devtunnels_home_dir,
    default_state_dir,
)
from synthorg.integrations.tunnel.cloudflare_adapter import (
    CloudflareQuickTunnelAdapter,
)
from synthorg.integrations.tunnel.devtunnels_adapter import DevTunnelsAdapter
from synthorg.integrations.tunnel.manager import TunnelManager
from synthorg.integrations.tunnel.ngrok_adapter import NgrokAdapter


def build_tunnel_manager(
    config: TunnelConfig, *, port: int, state_dir: Path | None = None
) -> TunnelManager:
    """Assemble the multi-provider tunnel manager.

    Args:
        config: Static tunnel config (env-fallback token var, binary
            download policy).
        port: The local API port every adapter exposes.
        state_dir: Root for tunnel runtime state; ``None`` means the
            bare-metal default ``~/.synthorg``.

    Returns:
        The manager, with Cloudflare quick tunnel as the default
        provider (accountless).
    """
    resolved_state_dir = state_dir if state_dir is not None else default_state_dir()
    binary_dir = default_binary_dir(resolved_state_dir)
    return TunnelManager(
        adapters=(
            CloudflareQuickTunnelAdapter(
                port=port,
                download_enabled=config.cloudflared_download_enabled,
                binary_dir=binary_dir,
            ),
            NgrokAdapter(auth_token_env=config.auth_token_env, port=port),
            DevTunnelsAdapter(
                port=port,
                download_enabled=config.devtunnel_download_enabled,
                binary_dir=binary_dir,
                home_dir=default_devtunnels_home_dir(resolved_state_dir),
            ),
        ),
    )
