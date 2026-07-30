"""Backup service factory: wiring helpers for app startup.

Dispatches per-component handler construction via
:data:`synthorg.backup.registry.PERSISTENCE_BACKUP_HANDLER_REGISTRY`, so
swapping SQLite for Postgres at deploy time picks up the backend-appropriate
``VACUUM INTO`` or ``pg_dump`` implementation without editing this file.

The persistence handler follows the backend that **connected**, falling back to
``config.persistence.backend`` only when nothing did. An env-driven deployment
(``SYNTHORG_DATABASE_URL``) creates its backend from a boot config assembled in
``api/boot_persistence`` and never writes that choice back into ``RootConfig``,
whose ``backend`` field defaults to ``sqlite``, so the config states an intent
the deployment may not be honouring. Backing up the wrong database is worse than
not backing one up, so reality outranks intent here, the same way
``resolved_db_path`` already outranks the configured path.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Final, assert_never

from synthorg.backup.config import BackupConfig
from synthorg.backup.handlers.config_handler import ConfigComponentHandler
from synthorg.backup.handlers.memory import MemoryComponentHandler
from synthorg.backup.handlers.protocol import ComponentHandler
from synthorg.backup.models import BackupComponent
from synthorg.backup.registry import PERSISTENCE_BACKUP_HANDLER_REGISTRY
from synthorg.backup.service import BackupService
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.protocol import PersistenceBackendKind
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

if TYPE_CHECKING:
    # Cycle breaker: ``config.schema`` sits on the eager-init config
    # chain and is named here for signatures only.
    from synthorg.config.schema import RootConfig

logger = get_logger(__name__)

# Default company-template filename when no config path was resolved at
# boot. ``SYNTHORG_CONFIG_PATH`` is read exactly once at the app boot
# site (``api/app.py``); a resolved value flows in via
# ``resolved_config_path`` so this module never re-reads the env var.
_DEFAULT_CONFIG_FILENAME: Final[str] = "company.yaml"


def _build_persistence_handler(
    config: RootConfig,
    resolved_db_path: Path | None,
    connected_backend_kind: PersistenceBackendKind | None,
) -> ComponentHandler:
    """Dispatch the persistence backup handler by backend discriminator.

    Returns:
        The ``ComponentHandler`` built for the backend that connected, or for
        the configured one when no backend did.
    """
    backend = (
        config.persistence.backend
        if connected_backend_kind is None
        else connected_backend_kind.value
    )
    if connected_backend_kind is not None and backend != config.persistence.backend:
        # The routine deployment shape, not a fault: the compose template
        # configures Postgres through the environment and leaves the YAML at its
        # default. Logged because a wrong dispatch here is invisible until a
        # scheduled backup fails hours later, and this line names the winner.
        logger.info(
            API_APP_STARTUP,
            component="backup_persistence_handler",
            note="backup handler bound to the connected backend, not the config",
            connected_backend=backend,
            configured_backend=config.persistence.backend,
        )
    return PERSISTENCE_BACKUP_HANDLER_REGISTRY.build(
        backend,
        config,
        resolved_db_path=resolved_db_path,
    )


def build_backup_handlers(
    config: RootConfig,
    backup_config: BackupConfig,
    *,
    resolved_db_path: Path | None = None,
    resolved_config_path: Path | None = None,
    connected_backend_kind: PersistenceBackendKind | None = None,
) -> dict[BackupComponent, ComponentHandler]:
    """Build component handlers from config and resolved runtime paths.

    Args:
        config: Root company configuration.
        backup_config: Backup-specific configuration.
        resolved_db_path: Actual DB path used by the persistence
            backend (SQLite only; ignored for Postgres). Falls back to
            ``config.persistence.sqlite.path``.
        resolved_config_path: Actual company YAML path loaded at
            startup (falls back to ``company.yaml`` when absent).
        connected_backend_kind: Discriminator of the backend that actually
            connected. Outranks ``config.persistence.backend``; ``None`` on a
            persistence-less boot, where the config is the only thing to go on.

    Returns:
        Handler map keyed by component enum.
    """
    handlers: dict[BackupComponent, ComponentHandler] = {}

    for component_name in backup_config.include:
        component = BackupComponent(component_name)
        if component is BackupComponent.PERSISTENCE:
            handlers[component] = _build_persistence_handler(
                config,
                resolved_db_path,
                connected_backend_kind,
            )
        elif component is BackupComponent.MEMORY:
            handlers[component] = MemoryComponentHandler(
                data_dir=Path(config.memory.storage.data_dir),
            )
        elif component is BackupComponent.CONFIG:
            cfg_path = resolved_config_path or Path(_DEFAULT_CONFIG_FILENAME)
            handlers[component] = ConfigComponentHandler(
                config_path=cfg_path,
            )
        else:  # pragma: no cover
            assert_never(component)

    return handlers


def build_backup_service(
    config: RootConfig,
    *,
    resolved_db_path: Path | None = None,
    resolved_config_path: Path | None = None,
    config_resolver: ConfigResolverProtocol | None = None,
    connected_backend_kind: PersistenceBackendKind | None = None,
) -> BackupService | None:
    """Create backup service from config.

    Uses resolved runtime paths when available so backups target
    the actual files the application opened at startup.

    The service is always constructed regardless of ``backup.enabled``
    so the registered ``backup.*`` settings have a live consumer at
    boot. ``BackupService.start()`` honours ``self._config.enabled``
    internally: when disabled the scheduler does not run and the
    settings subscriber path can flip the scheduler on at runtime
    without rebuilding the service.

    Args:
        config: Root company configuration.
        resolved_db_path: Actual DB path used by the persistence
            backend (SQLite only). Falls back to the config value.
        resolved_config_path: Actual company YAML path loaded at
            startup (falls back to ``company.yaml`` when absent).
        config_resolver: Optional resolver so the retention manager reads
            the live ``backup.retention_days`` setting (DB > env > code
            default) at prune time instead of only the static config.
        connected_backend_kind: Discriminator of the backend that actually
            connected, which outranks ``config.persistence.backend``.

    Returns:
        Configured backup service, or ``None`` if handler construction
        fails. Construction failures include invalid component paths
        and, for Postgres deployments, the ``pg_dump`` / ``pg_restore``
        binaries being absent from PATH (verified by the registry's
        factory dispatch before the handler is instantiated).
    """
    backup_config = config.backup
    try:
        handlers = build_backup_handlers(
            config,
            backup_config,
            resolved_db_path=resolved_db_path,
            resolved_config_path=resolved_config_path,
            connected_backend_kind=connected_backend_kind,
        )
        return BackupService(
            backup_config,
            handlers,
            config_resolver=config_resolver,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            note="Failed to build backup service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
