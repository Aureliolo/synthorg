"""Backup service factory -- wiring helpers for app startup."""

import os
from pathlib import Path
from typing import TYPE_CHECKING, assert_never

from synthorg.backup.handlers.config_handler import ConfigComponentHandler
from synthorg.backup.handlers.memory import MemoryComponentHandler
from synthorg.backup.handlers.persistence import PersistenceComponentHandler
from synthorg.backup.models import BackupComponent
from synthorg.backup.service import BackupService
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

if TYPE_CHECKING:
    from synthorg.backup.config import BackupConfig
    from synthorg.backup.handlers.protocol import ComponentHandler
    from synthorg.config.schema import RootConfig

logger = get_logger(__name__)


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
            backend (falls back to config value).
        resolved_config_path: Actual company YAML path loaded at
            startup (falls back to SYNTHORG_CONFIG_PATH / company.yaml).

    Returns:
        Handler map keyed by component enum.
    """
    handlers: dict[BackupComponent, ComponentHandler] = {}

    for component_name in backup_config.include:
        component = BackupComponent(component_name)
        if component is BackupComponent.PERSISTENCE:
            db_path = resolved_db_path or Path(config.persistence.sqlite.path)
            handlers[component] = PersistenceComponentHandler(
                db_path=db_path,
            )
        elif component is BackupComponent.MEMORY:
            handlers[component] = MemoryComponentHandler(
                data_dir=Path(config.memory.storage.data_dir),
            )
        elif component is BackupComponent.CONFIG:
            cfg_path = resolved_config_path or Path(
                os.environ.get("SYNTHORG_CONFIG_PATH", "company.yaml"),
            )
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
    internally -- when disabled the scheduler does not run and the
    settings subscriber path can flip the scheduler on at runtime
    without rebuilding the service.

    Args:
        config: Root company configuration.
        resolved_db_path: Actual DB path used by the persistence
            backend (falls back to config value).
        resolved_config_path: Actual company YAML path loaded at
            startup (falls back to SYNTHORG_CONFIG_PATH / company.yaml).

    Returns:
        Configured backup service, or ``None`` if handler construction
        fails (e.g. invalid component path).
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
    except MemoryError, RecursionError:
        raise
    except Exception:
        logger.warning(
            API_APP_STARTUP,
            error="Failed to build backup service",
        )
        return None
