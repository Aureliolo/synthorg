"""SQLite persistence backup handler.

Uses ``VACUUM INTO`` for consistent, point-in-time copies without
WAL/SHM complications. Registered in
:mod:`synthorg.backup.registry.PERSISTENCE_BACKUP_HANDLER_REGISTRY`
under the ``"sqlite"`` discriminator.
"""

import asyncio
import shutil
from pathlib import Path

from synthorg.backup.errors import ComponentBackupError
from synthorg.backup.models import BackupComponent
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.backup import (
    BACKUP_COMPONENT_COMPLETED,
    BACKUP_COMPONENT_FAILED,
    BACKUP_COMPONENT_STARTED,
)
from synthorg.persistence.sqlite.backup_utils import (
    IntegrityCheckError,
    integrity_check,
    vacuum_into,
)

logger = get_logger(__name__)

_DB_FILENAME = "synthorg.db"


class SQLitePersistenceComponentHandler:
    """Back up and restore the SQLite persistence database.

    Uses ``VACUUM INTO`` for consistent, point-in-time copies
    without WAL/SHM complications.

    Args:
        db_path: Path to the live SQLite database file.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    @property
    def component(self) -> BackupComponent:
        """Return the component this handler manages."""
        return BackupComponent.PERSISTENCE

    async def backup(self, target_dir: Path) -> int:
        """Create a VACUUM INTO copy of the database.

        Args:
            target_dir: Directory to write the backup database into.

        Returns:
            Size of the backup file in bytes.

        Raises:
            ComponentBackupError: If the backup operation fails.
        """
        logger.info(
            BACKUP_COMPONENT_STARTED,
            component=self.component.value,
            db_path=str(self._db_path),
        )
        target_file = target_dir / _DB_FILENAME
        try:
            size = await asyncio.to_thread(
                self._vacuum_into,
                str(self._db_path),
                str(target_file),
            )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger, BACKUP_COMPONENT_FAILED, exc, component=self.component.value
            )
            msg = f"Failed to back up persistence DB: {safe_error_description(exc)}"
            raise ComponentBackupError(msg) from exc
        logger.info(
            BACKUP_COMPONENT_COMPLETED,
            component=self.component.value,
            size_bytes=size,
        )
        return size

    async def restore(self, source_dir: Path) -> None:
        """Restore the database from a backup copy.

        Copies backup into place and validates with
        ``PRAGMA integrity_check``.  If the current DB exists, it
        is renamed to ``.bak`` first for atomic rollback on failure.
        Also removes stale WAL/SHM sidecar files to prevent
        WAL replay corruption.

        Args:
            source_dir: Directory containing the backup database.

        Raises:
            ComponentBackupError: If restore fails.
        """
        source_file = source_dir / _DB_FILENAME
        if not source_file.exists():
            logger.warning(
                BACKUP_COMPONENT_FAILED,
                component=self.component.value,
                error=f"Backup database not found: {source_file}",
            )
            msg = f"Backup database not found: {source_file}"
            raise ComponentBackupError(msg)

        bak_path = self._db_path.with_suffix(".db.bak")

        try:
            await asyncio.to_thread(
                self._atomic_swap, self._db_path, source_file, bak_path
            )
        except ComponentBackupError as exc:
            log_exception_redacted(
                logger, BACKUP_COMPONENT_FAILED, exc, component=self.component.value
            )
            raise
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger, BACKUP_COMPONENT_FAILED, exc, component=self.component.value
            )
            msg = f"Failed to restore persistence DB: {safe_error_description(exc)}"
            raise ComponentBackupError(msg) from exc

    async def validate_source(self, source_dir: Path) -> bool:
        """Validate that the backup database passes integrity check.

        Returns ``False`` when the integrity check itself reports
        corruption.  Any system-level failure (unreadable file, sqlite
        driver error, missing WAL/SHM permissions) propagates as a
        ``ComponentBackupError`` so callers can distinguish "the backup
        is corrupt" from "we could not determine whether the backup
        is corrupt".

        Args:
            source_dir: Directory containing the backup database.

        Returns:
            ``True`` if the database passed integrity check.

        Raises:
            ComponentBackupError: If the integrity check could not be
                run (I/O error, driver error, permission denied, etc.).
        """
        source_file = source_dir / _DB_FILENAME
        if not source_file.exists():
            return False
        try:
            return await asyncio.to_thread(
                self._check_integrity,
                str(source_file),
            )
        except IntegrityCheckError as exc:
            # Route through ``log_exception_redacted`` rather than
            # ``logger.exception``: the helper centralises the
            # error_type + scrubbed-message pair AND skips traceback
            # attachment, so frame-locals (potentially carrying any
            # in-scope credential) cannot reach the structured log
            # sink. The raised ComponentBackupError below carries the
            # same scrubbed description for the caller.
            log_exception_redacted(
                logger,
                BACKUP_COMPONENT_FAILED,
                exc,
                component=self.component.value,
                phase="integrity_check",
            )
            msg = f"Failed to run integrity check on backup: {safe_error_description(exc)}"  # noqa: E501
            raise ComponentBackupError(msg) from exc

    @staticmethod
    def _vacuum_into(source_path: str, target_path: str) -> int:
        """Delegate to the persistence-layer backup primitive.

        Returns:
            The number of bytes written by ``VACUUM INTO``.
        """
        return vacuum_into(source_path, target_path)

    @staticmethod
    def _check_integrity(db_path: str) -> bool:
        """Delegate to the persistence-layer integrity check.

        Returns:
            ``True`` when the database passes the integrity check.
        """
        return integrity_check(db_path)

    @staticmethod
    def _remove_sidecars(db_path: Path) -> None:
        """Remove WAL and SHM sidecar files for a database."""
        for suffix in ("-wal", "-shm"):
            sidecar = db_path.with_name(f"{db_path.name}{suffix}")
            if sidecar.exists():
                sidecar.unlink()

    @staticmethod
    def _atomic_swap(
        db_path: Path,
        source_file: Path,
        bak_path: Path,
    ) -> None:
        """Swap the live DB with the backup, rolling back on failure.

        Removes WAL/SHM sidecar files before opening the restored
        database to prevent stale WAL replay corruption.

        Single-process precondition: the move / copy / integrity-check
        sequence is not protected against a concurrent OS-level writer
        touching ``db_path`` between steps. Restore MUST run only when
        the application owns the database exclusively (the BackupService
        lock guarantees this for in-process callers; deployments that
        attach external backup tooling or share the SQLite file across
        processes need their own file-lock around the restore window
        before invoking this helper).

        Raises:
            ComponentBackupError: When the swap fails and the original
                database has been rolled back.
        """
        # Move current to .bak (including sidecars)
        if db_path.exists():
            shutil.move(db_path, bak_path)
        # Remove stale sidecars from the original location
        SQLitePersistenceComponentHandler._remove_sidecars(db_path)

        try:
            shutil.copy2(source_file, db_path)
            # Validate the restored copy via the persistence-layer helper.
            if not integrity_check(str(db_path)):
                msg = "Restored database failed integrity check"
                raise ComponentBackupError(msg)  # noqa: TRY301
        except Exception as exc:
            # Critical errors skip the rollback: it does filesystem work
            # that may allocate, and must not run under catastrophic
            # interpreter state.
            reraise_critical(exc)
            # Rollback: restore the original if we had one; otherwise wipe
            # the partially-copied (invalid) file so no bad DB remains.
            if bak_path.exists():
                if db_path.exists():
                    db_path.unlink()
                SQLitePersistenceComponentHandler._remove_sidecars(db_path)
                shutil.move(bak_path, db_path)
            elif db_path.exists():
                db_path.unlink()
                SQLitePersistenceComponentHandler._remove_sidecars(db_path)
            raise

        # Cleanup .bak on success
        if bak_path.exists():
            bak_path.unlink()
        # Remove any sidecars created during integrity check
        SQLitePersistenceComponentHandler._remove_sidecars(db_path)
