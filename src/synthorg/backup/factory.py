"""Backup service factory: wiring helpers for app startup.

Dispatches per-component handler construction via
:data:`synthorg.backup.registry.PERSISTENCE_BACKUP_HANDLER_REGISTRY`, so
swapping SQLite for Postgres at deploy time picks up the backend-appropriate
``VACUUM INTO`` or ``pg_dump`` implementation without editing this file.

The persistence handler follows the backend built at boot, falling back to
``config.persistence`` only when none was. An env-driven deployment
(``SYNTHORG_DATABASE_URL``) assembles its backend from a boot config in
``api/boot_persistence`` and never writes that choice back into ``RootConfig``,
whose ``backend`` defaults to ``sqlite`` and whose ``postgres`` block stays
``None``, so the config states an intent the deployment may not be honouring.
Backing up the wrong database is worse than not backing one up, so reality
outranks intent here, the same way ``resolved_db_path`` already outranks the
configured path.

Both halves of that reality come off one object: taking the whole backend rather
than its discriminator means the connection details cannot disagree with the kind
they are dispatched under, and it is the connection details that the Postgres
handler actually needs.
"""

from collections.abc import Callable
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
from synthorg.observability.events.backup import (
    BACKUP_HANDLER_BACKEND_MISMATCH,
    BACKUP_HANDLER_SELECTED,
    BACKUP_SERVICE_UNAVAILABLE,
)
from synthorg.persistence.protocol import PersistenceBackend, PersistenceBackendKind
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

if TYPE_CHECKING:
    # Cycle breaker: ``config.schema`` sits on the eager-init config
    # chain and is named here for signatures only.
    from synthorg.config.schema import RootConfig

logger = get_logger(__name__)

# Default company-template filename when no config path was resolved at
# boot. ``SYNTHORG_CONFIG_PATH`` is read exactly once, in
# ``api/boot_persistence``; a resolved value flows in via
# ``resolved_config_path`` so this module never re-reads the env var.
_DEFAULT_CONFIG_FILENAME: Final[str] = "company.yaml"


def _log_backend_choice(
    selected: str,
    configured: str,
    connected_kind: PersistenceBackendKind,
) -> None:
    """Record which backend the persistence handler bound to.

    A mismatch is logged at WARNING only when the configured backend is the
    more durable of the two. Postgres-in-env over a default SQLite YAML is the
    routine compose shape and says nothing is wrong, but an operator who wrote
    ``postgres`` into ``company.yaml`` and got SQLite has a migration that did
    not take effect, and this is the only place in the system where that
    surfaces at all.
    """
    if selected == configured:
        logger.info(
            BACKUP_HANDLER_SELECTED,
            component="backup_persistence_handler",
            selected_backend=selected,
        )
        return
    downgraded = (
        connected_kind is PersistenceBackendKind.SQLITE
        and configured == PersistenceBackendKind.POSTGRES.value
    )
    log = logger.warning if downgraded else logger.info
    log(
        BACKUP_HANDLER_BACKEND_MISMATCH,
        component="backup_persistence_handler",
        selected_backend=selected,
        configured_backend=configured,
        durability_downgraded=downgraded,
    )


def _build_persistence_handler(
    config: RootConfig,
    resolved_db_path: Path | None,
    boot_backend: PersistenceBackend | None,
) -> ComponentHandler:
    """Dispatch the persistence backup handler by backend discriminator.

    Returns:
        The ``ComponentHandler`` built for the backend assembled at boot, or
        for the configured one when none was.
    """
    if boot_backend is None:
        selected = config.persistence.backend
        connected_config = None
    else:
        selected = boot_backend.kind.value
        connected_config = boot_backend.config
        _log_backend_choice(selected, config.persistence.backend, boot_backend.kind)
    return PERSISTENCE_BACKUP_HANDLER_REGISTRY.build(
        selected,
        config,
        resolved_db_path=resolved_db_path,
        connected_config=connected_config,
    )


def build_backup_handlers(
    config: RootConfig,
    backup_config: BackupConfig,
    *,
    resolved_db_path: Path | None = None,
    resolved_config_path: Path | None = None,
    boot_backend: PersistenceBackend | None = None,
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
        boot_backend: The persistence backend assembled at boot. Its kind and
            its connection details both outrank ``config.persistence``; ``None``
            on a persistence-less boot, where the config is the only thing to
            go on.

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
                boot_backend,
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
    boot_backend: PersistenceBackend | None = None,
    on_unavailable: Callable[[str], None] | None = None,
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
        boot_backend: The persistence backend assembled at boot, whose kind and
            connection details outrank ``config.persistence``.
        on_unavailable: Called with the redacted failure description when
            construction fails. The reason exists only inside this handler,
            and a caller that wants to report it has no other way to learn
            it: the return is ``None`` either way. Passed as a callback
            rather than returned alongside the service so the signature
            stays ``BackupService | None``, which is the shape the
            settings-to-startup trace gate matches to detect a
            factory-gated ghost.

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
            boot_backend=boot_backend,
        )
        return BackupService(
            backup_config,
            handlers,
            config_resolver=config_resolver,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        # ERROR, not WARNING: a ``None`` service means this boot has no backup
        # coverage at all and no ``backup.*`` setting has a live consumer, for
        # the whole process lifetime. That is a standing operational condition,
        # not a transient hiccup, and ``/health`` reports it via
        # ``BackupStateSlice``.
        description = safe_error_description(exc)
        logger.error(
            BACKUP_SERVICE_UNAVAILABLE,
            component="backup_service",
            error_type=type(exc).__name__,
            error=description,
        )
        if on_unavailable is not None:
            on_unavailable(description)
        return None
