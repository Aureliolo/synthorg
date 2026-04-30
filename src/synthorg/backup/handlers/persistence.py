"""Persistence component handler -- SQLite VACUUM INTO backup."""

import asyncio
import shutil
from pathlib import Path  # noqa: TC003

from synthorg.backup.errors import ComponentBackupError
from synthorg.backup.models import BackupComponent
from synthorg.observability import get_logger, safe_error_description
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


class PersistenceComponentHandler:
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
            logger.error(  # noqa: TRY400
                BACKUP_COMPONENT_FAILED,
                component=self.component.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to back up persistence DB: {exc}"
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
            logger.error(  # noqa: TRY400
                BACKUP_COMPONENT_FAILED,
                component=self.component.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        except Exception as exc:
            logger.error(  # noqa: TRY400
                BACKUP_COMPONENT_FAILED,
                component=self.component.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to restore persistence DB: {exc}"
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
            logger.error(
                BACKUP_COMPONENT_FAILED,
                component=self.component.value,
                error=f"Integrity check could not run: {exc}",
                exc_info=True,
            )
            msg = f"Failed to run integrity check on backup: {exc}"
            raise ComponentBackupError(msg) from exc

    @staticmethod
    def _vacuum_into(source_path: str, target_path: str) -> int:
        """Delegate to the persistence-layer backup primitive."""
        return vacuum_into(source_path, target_path)

    @staticmethod
    def _check_integrity(db_path: str) -> bool:
        """Delegate to the persistence-layer integrity check."""
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
        """
        # Move current to .bak (including sidecars)
        if db_path.exists():
            shutil.move(db_path, bak_path)
        # Remove stale sidecars from the original location
        PersistenceComponentHandler._remove_sidecars(db_path)

        try:
            shutil.copy2(source_file, db_path)
            # Validate the restored copy via the persistence-layer helper.
            if not integrity_check(str(db_path)):
                msg = "Restored database failed integrity check"
                raise ComponentBackupError(msg)  # noqa: TRY301
        except Exception:
            # Rollback: restore the original if we had one; otherwise wipe
            # the partially-copied (invalid) file so no bad DB remains.
            if bak_path.exists():
                if db_path.exists():
                    db_path.unlink()
                PersistenceComponentHandler._remove_sidecars(db_path)
                shutil.move(bak_path, db_path)
            elif db_path.exists():
                db_path.unlink()
                PersistenceComponentHandler._remove_sidecars(db_path)
            raise

        # Cleanup .bak on success
        if bak_path.exists():
            bak_path.unlink()
        # Remove any sidecars created during integrity check
        PersistenceComponentHandler._remove_sidecars(db_path)
