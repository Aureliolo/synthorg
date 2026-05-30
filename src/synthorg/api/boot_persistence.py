# module-kind: code
"""Boot-time persistence + artifact-storage resolution from the environment.

``create_app`` calls :func:`resolve_boot_persistence` to auto-wire the
persistence backend and artifact storage from the CLI-provided environment
variables (``SYNTHORG_DATABASE_URL`` / ``SYNTHORG_DB_PATH``), unless the caller
injected them. The env vars are read unconditionally so downstream code can
observe which environment choice won even when persistence was injected.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from synthorg.api.app_helpers import _resolve_artifact_dir_env
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.artifact_storage import ArtifactStorageBackend
from synthorg.persistence.config_factory import (
    build_filesystem_artifact_storage,
    build_postgres_persistence_config_from_url,
    build_sqlite_persistence_config,
    normalize_ssl_mode_value,
)
from synthorg.persistence.factory import create_backend
from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)


@dataclass(frozen=True)
class BootPersistence:
    """Persistence + artifact storage + runtime paths resolved at boot.

    ``db_url`` / ``db_path`` carry the raw env-var values (empty when unset) so
    downstream wiring (integrations, backup) can observe the environment choice
    even when ``persistence`` was injected rather than auto-wired.
    """

    persistence: PersistenceBackend | None
    artifact_storage: ArtifactStorageBackend | None
    resolved_db_path: Path | None
    resolved_config_path: Path | None
    db_url: str
    db_path: str


def resolve_boot_persistence(
    *,
    persistence: PersistenceBackend | None,
    artifact_storage: ArtifactStorageBackend | None,
) -> BootPersistence:
    """Resolve persistence + artifact storage from injection or the environment.

    Auto-wires from the CLI compose template's env vars when ``persistence`` is
    not injected: Postgres (``SYNTHORG_DATABASE_URL``) takes precedence over
    SQLite (``SYNTHORG_DB_PATH``) so a half-converted state does not silently
    fall back to SQLite. The startup lifecycle handles connect() + migrate().

    Args:
        persistence: An injected backend (kept as-is) or ``None`` to auto-wire.
        artifact_storage: An injected artifact backend or ``None`` to auto-wire
            alongside an auto-wired persistence backend.

    Returns:
        The resolved persistence bundle (backends, runtime paths, raw env vars).

    Raises:
        Exception: Re-raised when backend creation fails (after redacted log).
    """
    resolved_db_path: Path | None = None
    resolved_config_path_str = (os.environ.get("SYNTHORG_CONFIG_PATH") or "").strip()
    resolved_config_path: Path | None = (
        Path(resolved_config_path_str) if resolved_config_path_str else None
    )

    db_url = (os.environ.get("SYNTHORG_DATABASE_URL") or "").strip()
    db_path = (os.environ.get("SYNTHORG_DB_PATH") or "").strip()

    if persistence is None:
        if db_url:
            try:
                pg_persistence_config = build_postgres_persistence_config_from_url(
                    db_url,
                    ssl_mode_override=normalize_ssl_mode_value(
                        os.environ.get("SYNTHORG_POSTGRES_SSL_MODE"),
                    ),
                )
                persistence = create_backend(pg_persistence_config)
            except Exception as exc:
                reraise_critical(exc)
                log_exception_redacted(
                    logger,
                    API_APP_STARTUP,
                    exc,
                    note="Postgres persistence creation failed",
                )
                raise
            assert pg_persistence_config.postgres is not None  # noqa: S101
            logger.info(
                API_APP_STARTUP,
                note="Auto-wired Postgres persistence from SYNTHORG_DATABASE_URL",
                host=pg_persistence_config.postgres.host,
                database=pg_persistence_config.postgres.database,
            )
            # Postgres has no on-disk artifact directory tied to the DB path, so
            # default artifact storage to /data (the CLI compose data volume).
            if artifact_storage is None:
                artifact_dir_str = _resolve_artifact_dir_env()
                artifact_storage = build_filesystem_artifact_storage(
                    data_dir=Path(artifact_dir_str),
                )
                logger.info(
                    API_APP_STARTUP,
                    note="Auto-wired filesystem artifact storage (postgres mode)",
                    data_dir=artifact_dir_str,
                )
        elif db_path:
            resolved_db_path = Path(db_path)
            try:
                persistence = create_backend(
                    build_sqlite_persistence_config(path=db_path),
                )
            except Exception as exc:
                reraise_critical(exc)
                log_exception_redacted(
                    logger,
                    API_APP_STARTUP,
                    exc,
                    note="Failed to create persistence backend from env",
                )
                raise
            logger.info(
                API_APP_STARTUP,
                note="Auto-wired SQLite persistence from SYNTHORG_DB_PATH",
                db_name=Path(db_path).name,
            )
            if artifact_storage is None:
                artifact_storage = build_filesystem_artifact_storage(
                    data_dir=resolved_db_path.parent,
                )
                logger.info(
                    API_APP_STARTUP,
                    note="Auto-wired filesystem artifact storage",
                )

    return BootPersistence(
        persistence=persistence,
        artifact_storage=artifact_storage,
        resolved_db_path=resolved_db_path,
        resolved_config_path=resolved_config_path,
        db_url=db_url,
        db_path=db_path,
    )
