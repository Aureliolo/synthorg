"""Tunnel wiring helpers for the integrations auto-wire pass.

Sibling of :mod:`synthorg.api.integrations_wiring`, which stays focused
on the catalog / OAuth / webhook / health-prober surface and delegates
the tunnel-specific pieces here.
"""

from pathlib import Path

from synthorg.config.schema import RootConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.tunnel.manager import TunnelManager
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_SERVICE_AUTO_WIRED,
)

logger = get_logger(__name__)


def resolve_tunnel_state_dir() -> Path | None:
    """Resolve ``integrations.tunnel_state_dir`` (env-seeded, boot-only).

    Returns:
        The state-dir path, or ``None`` when unset (adapters fall back
        to ``~/.synthorg``).

    Raises:
        ValueError: When the value carries a ``..`` traversal component.
    """
    from pathlib import PurePath  # noqa: PLC0415

    from synthorg.settings.bootstrap_resolver import (  # noqa: PLC0415
        resolve_init_value,
    )
    from synthorg.settings.enums import SettingNamespace  # noqa: PLC0415

    raw = str(
        resolve_init_value(SettingNamespace.INTEGRATIONS, "tunnel_state_dir").value
    )
    if ".." in PurePath(raw).parts:
        msg = (
            f"SYNTHORG_TUNNEL_STATE_DIR contains '..' path traversal component: {raw!r}"
        )
        raise ValueError(msg)
    return Path(raw) if raw else None


def wire_tunnel_provider(effective_config: RootConfig) -> TunnelManager | None:
    """Wire the multi-provider tunnel manager (no persistence dep).

    Best-effort: a missing adapter module or constructor failure must
    not abort app startup. The dashboard's tunnel card simply degrades
    to "unavailable" when this returns ``None``. The tunneled port is
    the API's own resolved serving port (``api.server_port``), so
    every provider exposes the address the server actually listens on.
    The state dir (``integrations.tunnel_state_dir``, env
    ``SYNTHORG_TUNNEL_STATE_DIR``) roots downloaded binaries and the
    devtunnel login home; a ``..`` component is rejected so the env
    var cannot traverse out of its mount.

    Returns:
        The ``TunnelManager`` value when present, ``None`` otherwise.
    """
    try:
        from synthorg.integrations.tunnel.factory import (  # noqa: PLC0415
            build_tunnel_manager,
        )
        from synthorg.settings.bootstrap_resolver import (  # noqa: PLC0415
            resolve_init_value,
        )
        from synthorg.settings.enums import SettingNamespace  # noqa: PLC0415
        from synthorg.settings.mirrors import parse_int  # noqa: PLC0415

        port = int(
            resolve_init_value(
                SettingNamespace.API,
                "server_port",
                parse=parse_int,
            ).value
        )
        provider = build_tunnel_manager(
            effective_config.integrations.tunnel,
            port=port,
            state_dir=resolve_tunnel_state_dir(),
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="tunnel_provider",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="tunnel provider auto-wire failed (non-fatal)",
        )
        return None
    logger.info(API_SERVICE_AUTO_WIRED, service="tunnel_provider")
    return provider


def bind_tunnel_connection_health(manager: TunnelManager | None) -> None:
    """Route tunnel-connection health through the tunnel manager.

    The Connections screen must report the same verdict as the tunnel
    card, so the tunnel connection checker resolves readiness from the
    manager; connections outside the ``tunnel-<provider>`` convention
    (and every connection when no manager is wired) resolve to ``None``
    and report ``UNKNOWN``.
    """
    if manager is None:
        return
    from synthorg.integrations.health.prober import (  # noqa: PLC0415
        bind_tunnel_status_lookup,
    )
    from synthorg.integrations.tunnel.manager import (  # noqa: PLC0415
        tunnel_provider_id_for_connection,
    )
    from synthorg.integrations.tunnel.protocol import (  # noqa: PLC0415
        TunnelProviderStatus,
    )

    async def _lookup(connection_name: str) -> TunnelProviderStatus | None:
        provider_id = tunnel_provider_id_for_connection(connection_name)
        if provider_id is None:
            return None
        return await manager.provider_status(provider_id)

    bind_tunnel_status_lookup(_lookup)
