"""Backup service factory: wiring helpers for app startup.

Dispatches per-component handler construction. The persistence
handler is selected by ``config.persistence.backend`` via
:data:`synthorg.backup.registry.PERSISTENCE_BACKUP_HANDLER_REGISTRY`,
so swapping SQLite for Postgres at deploy time picks up the
backend-appropriate ``VACUUM INTO`` or ``pg_dump`` implementation
without editing this file.
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
) -> ComponentHandler:
    """Dispatch the persistence backup handler by backend discriminator.

    Returns:
        The ``ComponentHandler`` built for the configured persistence
        backend.
    """
    return PERSISTENCE_BACKUP_HANDLER_REGISTRY.build(
        config.persistence.backend,
        config,
        resolved_db_path=resolved_db_path,
    )


def build_backup_handlers(
    config: RootConfig,
    backup_config: BackupConfig,
    *,
    resolved_db_path: Path | None = None,
    resolved_config_path: Path | None = None,
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
        )
        return BackupService(backup_config, handlers)
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            note="Failed to build backup service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
