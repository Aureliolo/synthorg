"""Postgres persistence backup handler.

Uses ``pg_dump`` for backup and ``pg_restore`` for restore via the
helpers in :mod:`synthorg.persistence.postgres.backup_utils`. Registered
in
:mod:`synthorg.backup.registry.PERSISTENCE_BACKUP_HANDLER_REGISTRY`
under the ``"postgres"`` discriminator.
"""

from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Final

from synthorg.backup.errors import ComponentBackupError
from synthorg.backup.models import BackupComponent
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.backup import (
    BACKUP_COMPONENT_COMPLETED,
    BACKUP_COMPONENT_FAILED,
    BACKUP_COMPONENT_STARTED,
)
from synthorg.persistence.postgres.backup_utils import (
    PgToolFailedError,
    PgToolUnavailableError,
    pg_dump_to_file,
    pg_restore_from_file,
    pg_restore_list,
)

if TYPE_CHECKING:
    from synthorg.persistence.config import PostgresConfig

logger = get_logger(__name__)

_DUMP_FILENAME: Final[str] = "synthorg.pgdump"


class PostgresPersistenceComponentHandler:
    """Back up and restore a Postgres persistence database.

    Args:
        config: PostgresConfig identifying the live database. Connection
            details (host, port, username, password) are reused for
            ``pg_dump`` / ``pg_restore`` invocations; the password is
            injected via ``PGPASSWORD`` so it never appears on argv.
    """

    def __init__(self, config: PostgresConfig) -> None:
        self._config = config

    @property
    def component(self) -> BackupComponent:
        """Return the component this handler manages."""
        return BackupComponent.PERSISTENCE

    async def backup(self, target_dir: Path) -> int:
        """Run ``pg_dump -Fc`` into ``target_dir/synthorg.pgdump``.

        Args:
            target_dir: Directory to write the dump file into.

        Returns:
            Size of the dump file in bytes.

        Raises:
            ComponentBackupError: ``pg_dump`` is unavailable or failed.
        """
        logger.info(
            BACKUP_COMPONENT_STARTED,
            component=self.component.value,
            database=self._config.database,
            host=self._config.host,
        )
        target_file = target_dir / _DUMP_FILENAME
        try:
            size = await pg_dump_to_file(self._config, target_file)
        except (PgToolUnavailableError, PgToolFailedError) as exc:
            logger.error(
                BACKUP_COMPONENT_FAILED,
                component=self.component.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to back up Postgres DB: {safe_error_description(exc)}"
            raise ComponentBackupError(msg) from exc
        except TimeoutError as exc:
            logger.error(
                BACKUP_COMPONENT_FAILED,
                component=self.component.value,
                error_type="TimeoutError",
                error=safe_error_description(exc),
            )
            msg = "pg_dump timed out"
            raise ComponentBackupError(msg) from exc
        logger.info(
            BACKUP_COMPONENT_COMPLETED,
            component=self.component.value,
            size_bytes=size,
        )
        return size

    async def restore(self, source_dir: Path) -> None:
        """Restore the database from ``source_dir/synthorg.pgdump``.

        Uses ``pg_restore --clean --if-exists --single-transaction`` so
        a partial failure leaves the database unchanged.

        Args:
            source_dir: Directory containing the backup dump.

        Raises:
            ComponentBackupError: Dump file missing, ``pg_restore``
                unavailable, or restore failed.
        """
        source_file = source_dir / _DUMP_FILENAME
        if not source_file.exists():
            logger.warning(
                BACKUP_COMPONENT_FAILED,
                component=self.component.value,
                error=f"Postgres dump not found: {source_file}",
            )
            msg = f"Postgres dump not found: {source_file}"
            raise ComponentBackupError(msg)
        try:
            await pg_restore_from_file(self._config, source_file)
        except (PgToolUnavailableError, PgToolFailedError) as exc:
            logger.error(
                BACKUP_COMPONENT_FAILED,
                component=self.component.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to restore Postgres DB: {safe_error_description(exc)}"
            raise ComponentBackupError(msg) from exc
        except TimeoutError as exc:
            logger.error(
                BACKUP_COMPONENT_FAILED,
                component=self.component.value,
                error_type="TimeoutError",
                error=safe_error_description(exc),
            )
            msg = "pg_restore timed out"
            raise ComponentBackupError(msg) from exc

    async def validate_source(self, source_dir: Path) -> bool:
        """Validate that *source_dir* contains a readable dump.

        Reads the dump's TOC via ``pg_restore --list``. Returns ``True``
        when the listing succeeds and contains at least one entry.

        Args:
            source_dir: Directory to validate.

        Returns:
            ``True`` if the dump is structurally readable, ``False`` if
            the dump file is missing or empty.

        Raises:
            ComponentBackupError: ``pg_restore`` is unavailable or the
                listing itself failed (distinct from "dump is missing").
        """
        source_file = source_dir / _DUMP_FILENAME
        if not source_file.exists():
            return False
        try:
            entry_count = await pg_restore_list(source_file)
        except (PgToolUnavailableError, PgToolFailedError) as exc:
            logger.error(
                BACKUP_COMPONENT_FAILED,
                component=self.component.value,
                phase="validate_source",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to validate Postgres dump: {safe_error_description(exc)}"
            raise ComponentBackupError(msg) from exc
        except TimeoutError as exc:
            logger.error(
                BACKUP_COMPONENT_FAILED,
                component=self.component.value,
                phase="validate_source",
                error_type="TimeoutError",
                error=safe_error_description(exc),
            )
            msg = "pg_restore --list timed out"
            raise ComponentBackupError(msg) from exc
        return entry_count > 0
