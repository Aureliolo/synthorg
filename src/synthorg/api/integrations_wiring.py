"""Auto-wiring helpers for MCP + integration services.

Keeps the integration-heavy auto-wire logic out of :mod:`synthorg.api.app`
so ``create_app`` stays under the file-size budget.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING

from synthorg.core.persistence_errors import PersistenceConnectionError
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_SERVICE_AUTO_WIRED,
)

if TYPE_CHECKING:
    from synthorg.api.config import ApiConfig
    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.config.schema import RootConfig
    from synthorg.engine.workflow.ceremony_scheduler import CeremonyScheduler
    from synthorg.engine.workflow.webhook_bridge import WebhookEventBridge
    from synthorg.integrations.connections.catalog import ConnectionCatalog
    from synthorg.integrations.health.prober import HealthProberService
    from synthorg.integrations.mcp_catalog.installations import (
        McpInstallationRepository,
    )
    from synthorg.integrations.mcp_catalog.service import CatalogService
    from synthorg.integrations.oauth.token_manager import OAuthTokenManager
    from synthorg.integrations.tunnel.protocol import TunnelProvider
    from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)


@dataclass
class IntegrationsBundle:
    """Services auto-wired by :func:`auto_wire_integrations`."""

    connection_catalog: ConnectionCatalog | None = None
    oauth_token_manager: OAuthTokenManager | None = None
    health_prober_service: HealthProberService | None = None
    tunnel_provider: TunnelProvider | None = None
    webhook_event_bridge: WebhookEventBridge | None = None
    mcp_catalog_service: CatalogService | None = None
    mcp_installations_repo: McpInstallationRepository | None = None
    _unused: tuple[str, ...] = field(default_factory=tuple)


def _wire_mcp_catalog() -> CatalogService | None:
    """Wire the bundled MCP catalog service (stateless)."""
    try:
        from synthorg.integrations.mcp_catalog.service import (  # noqa: PLC0415
            CatalogService,
        )

        service = CatalogService()
    except MemoryError, RecursionError:
        raise
    except Exception:
        logger.warning(
            API_APP_STARTUP,
            error="MCP catalog auto-wire failed (non-fatal)",
        )
        return None
    else:
        logger.info(API_SERVICE_AUTO_WIRED, service="mcp_catalog_service")
        return service


def _wire_mcp_installations_repo(
    persistence: PersistenceBackend | None,
) -> McpInstallationRepository | None:
    """Wire the MCP installations repo if persistence is already connected."""
    try:
        if persistence is not None and getattr(persistence, "is_connected", False):
            repo = persistence.mcp_installations
            logger.info(
                API_SERVICE_AUTO_WIRED,
                service="mcp_installations_repo",
                backend=type(persistence).__name__,
            )
            return repo
        if persistence is None:
            from synthorg.integrations.mcp_catalog.in_memory_installations import (  # noqa: PLC0415
                InMemoryMcpInstallationRepository,
            )

            repo = InMemoryMcpInstallationRepository()
            logger.debug(
                API_SERVICE_AUTO_WIRED,
                service="mcp_installations_repo",
                backend="in_memory",
            )
            return repo
        logger.debug(
            API_SERVICE_AUTO_WIRED,
            service="mcp_installations_repo",
            backend="deferred_until_persistence_connected",
        )
    except MemoryError, RecursionError:
        raise
    except Exception:
        logger.warning(
            API_APP_STARTUP,
            error="MCP installations repo auto-wire failed (non-fatal)",
        )
    return None


def _wire_tunnel_provider(auth_token_env: str) -> TunnelProvider | None:
    """Wire the tunnel adapter unconditionally (no persistence dep).

    Best-effort: a missing adapter module or constructor failure must
    not abort app startup. The dashboard's tunnel toggle simply
    degrades to "unavailable" when this returns ``None``.
    """
    try:
        from synthorg.integrations.tunnel.ngrok_adapter import (  # noqa: PLC0415
            NgrokAdapter,
        )

        provider = NgrokAdapter(auth_token_env=auth_token_env)
    except MemoryError, RecursionError:
        raise
    except Exception:
        logger.warning(
            API_APP_STARTUP,
            note="tunnel provider auto-wire failed (non-fatal)",
        )
        return None
    logger.info(API_SERVICE_AUTO_WIRED, service="tunnel_provider")
    return provider


def _resolve_secret_db_path(
    persistence: PersistenceBackend,
    *,
    resolved_db_path: Path | None,
    db_url: str,
) -> str | None:
    """Resolve the SQLite path for the encrypted_sqlite secret backend.

    Returns ``None`` in postgres mode (no SQLite file exists).
    """
    postgres_mode = bool(db_url)
    if resolved_db_path is not None:
        return str(resolved_db_path)
    if postgres_mode:
        return None

    injected_cfg = getattr(persistence, "_config", None)
    injected_path = getattr(injected_cfg, "path", None)
    if isinstance(injected_path, str) and injected_path and injected_path != ":memory:":
        return injected_path
    env_db_path = (os.environ.get("SYNTHORG_DB_PATH") or "").strip()
    return env_db_path or None


def _wire_rate_limit_coordinator_factory(
    *,
    message_bus: MessageBus,
    connection_catalog: ConnectionCatalog,
    api_config: ApiConfig,
) -> None:
    """Wire the shared rate-limit coordinator factory."""
    from synthorg.integrations.rate_limiting.shared_state import (  # noqa: PLC0415
        SharedRateLimitCoordinator,
        set_coordinator_factory_sync,
    )

    _bus = message_bus
    _catalog = connection_catalog
    _default_rpm = api_config.rate_limit.max_rpm_default

    def _make_coordinator(name: str) -> SharedRateLimitCoordinator:
        max_rpm = _default_rpm
        try:
            conn = _catalog.get_cached(name)
            if (
                conn is not None
                and conn.rate_limiter is not None
                and conn.rate_limiter.max_requests_per_minute > 0
            ):
                max_rpm = conn.rate_limiter.max_requests_per_minute
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                API_SERVICE_AUTO_WIRED,
                service="rate_limit_coordinator_factory",
                note=(
                    "could not read rate_limit_rpm from catalog cache; using default"
                ),
                connection_name=name,
            )
        return SharedRateLimitCoordinator(
            bus=_bus,
            connection_name=name,
            max_rpm=max_rpm,
        )

    set_coordinator_factory_sync(_make_coordinator)
    logger.info(
        API_SERVICE_AUTO_WIRED,
        service="rate_limit_coordinator_factory",
    )


def auto_wire_integrations(  # noqa: PLR0913
    *,
    effective_config: RootConfig,
    persistence: PersistenceBackend | None,
    message_bus: MessageBus | None,
    api_config: ApiConfig,
    ceremony_scheduler: CeremonyScheduler | None,
    db_url: str,
    resolved_db_path: Path | None,
) -> IntegrationsBundle:
    """Wire the MCP catalog, installations repo, and integration services.

    Best-effort: each stage logs and swallows non-fatal errors so the
    app still boots with integrations disabled when a dependency is
    missing.
    """
    bundle = IntegrationsBundle(
        mcp_catalog_service=_wire_mcp_catalog(),
        mcp_installations_repo=_wire_mcp_installations_repo(persistence),
    )

    # Wired unconditionally (no persistence / OAuth deps) so the
    # dashboard toggle works even when the rest of integrations are
    # disabled.
    bundle.tunnel_provider = _wire_tunnel_provider(
        effective_config.integrations.tunnel.auth_token_env,
    )

    if not (effective_config.integrations.enabled and persistence is not None):
        return bundle

    try:
        from synthorg.integrations.connections.catalog import (  # noqa: PLC0415
            ConnectionCatalog,
        )
        from synthorg.integrations.health.prober import (  # noqa: PLC0415
            HealthProberService,
            bind_health_check_catalog,
        )
        from synthorg.integrations.oauth.token_manager import (  # noqa: PLC0415
            OAuthTokenManager,
        )
        from synthorg.persistence.secret_backends.factory import (  # noqa: PLC0415
            create_secret_backend,
            resolve_secret_backend_config,
        )

        postgres_mode = bool(db_url)
        secret_db_path = _resolve_secret_db_path(
            persistence,
            resolved_db_path=resolved_db_path,
            db_url=db_url,
        )
        pg_pool_getter = persistence.get_db if postgres_mode else None

        selection = resolve_secret_backend_config(
            effective_config.integrations.secret_backend,
            postgres_mode=postgres_mode,
            pg_pool_available=pg_pool_getter is not None,
            sqlite_db_path=secret_db_path,
        )
        if selection.reason:
            log_fn = {
                "info": logger.info,
                "warning": logger.warning,
                "error": logger.error,
            }.get(selection.level, logger.info)
            log_fn(API_APP_STARTUP, note=selection.reason)
        secret_backend = create_secret_backend(
            selection.config,
            db_path=secret_db_path,
            pg_pool=pg_pool_getter,
        )
        try:
            connections_repo = persistence.connections
        except PersistenceConnectionError:
            # ``auto_wire_integrations`` runs before ``persistence.connect()``
            # in the ``create_app`` boot path; the lifecycle hook does the
            # connect inside ``_safe_startup``. The durable connection repos
            # require a live connection, so fall back to the in-memory stub
            # for the wiring window. The controllers still need an instance
            # attached to the bundle so they register on the app; OpenAPI
            # export depends on that registration.
            from synthorg.persistence.integration_stubs import (  # noqa: PLC0415
                InMemoryConnectionRepository,
            )

            connections_repo = InMemoryConnectionRepository()
            logger.warning(
                API_APP_STARTUP,
                note=(
                    "persistence not yet connected; using in-memory "
                    "connection repository for integrations wiring"
                ),
            )
        bundle.connection_catalog = ConnectionCatalog(
            repository=connections_repo,
            secret_backend=secret_backend,
        )
        bind_health_check_catalog(bundle.connection_catalog)
        logger.info(API_SERVICE_AUTO_WIRED, service="connection_catalog")

        health_cfg = effective_config.integrations.health
        bundle.health_prober_service = HealthProberService(
            catalog=bundle.connection_catalog,
            interval_seconds=health_cfg.check_interval_seconds,
            unhealthy_threshold=health_cfg.unhealthy_threshold,
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="health_prober_service")

        bundle.oauth_token_manager = OAuthTokenManager(
            catalog=bundle.connection_catalog,
            refresh_threshold_seconds=effective_config.integrations.oauth.auto_refresh_threshold_seconds,
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="oauth_token_manager")

        if message_bus is not None and ceremony_scheduler is not None:
            from synthorg.engine.workflow.webhook_bridge import (  # noqa: PLC0415
                WebhookEventBridge,
            )

            bundle.webhook_event_bridge = WebhookEventBridge(
                bus=message_bus,
                ceremony_scheduler=ceremony_scheduler,
            )
            logger.info(
                API_SERVICE_AUTO_WIRED,
                service="webhook_event_bridge",
            )

        if message_bus is not None:
            _wire_rate_limit_coordinator_factory(
                message_bus=message_bus,
                connection_catalog=bundle.connection_catalog,
                api_config=api_config,
            )
    except MemoryError, RecursionError:
        raise
    except Exception:
        logger.warning(
            API_APP_STARTUP,
            error="Integration services auto-wire failed (non-fatal)",
        )

    return bundle
