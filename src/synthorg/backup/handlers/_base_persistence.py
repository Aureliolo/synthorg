"""Shared scaffold for persistence backup component handlers.

The SQLite and Postgres persistence handlers share the same
orchestration: emit ``BACKUP_COMPONENT_STARTED``, run a backend-specific
mechanism, then emit ``BACKUP_COMPONENT_COMPLETED`` with the byte count.
Restore and validate share the same "missing source file" handling. Only
the mechanism (and its backend-specific error translation) differs, so it
lives in the subclass ``_do_backup`` / ``_do_restore`` / ``_do_validate``
hooks while the scaffold lives here once.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from synthorg.backup.errors import ComponentBackupError
from synthorg.backup.models import BackupComponent
from synthorg.observability import get_logger
from synthorg.observability.events.backup import (
    BACKUP_COMPONENT_COMPLETED,
    BACKUP_COMPONENT_FAILED,
    BACKUP_COMPONENT_STARTED,
)

logger = get_logger(__name__)


class BasePersistenceComponentHandler(ABC):
    """Template for persistence-database backup/restore handlers.

    Owns the shared start/complete logging, the backup-file name
    resolution, and the "missing source file" handling for restore and
    validate. Subclasses override only the backend-specific mechanism and
    its error translation via the ``_do_*`` hooks; the byte count returned
    by ``backup`` is observational (see
    :class:`~synthorg.backup.handlers.protocol.ComponentHandler`).
    """

    _filename: ClassVar[str]

    @property
    def component(self) -> BackupComponent:
        """Return the component this handler manages."""
        return BackupComponent.PERSISTENCE

    async def backup(self, target_dir: Path) -> int:
        """Back up the persistence database into *target_dir*.

        Args:
            target_dir: Directory to write the backup file into.

        Returns:
            Size of the backup file in bytes.

        Raises:
            ComponentBackupError: If the backup mechanism fails.
        """
        logger.info(
            BACKUP_COMPONENT_STARTED,
            component=self.component.value,
            **self._started_log_fields(),
        )
        size = await self._do_backup(target_dir / self._filename)
        logger.info(
            BACKUP_COMPONENT_COMPLETED,
            component=self.component.value,
            size_bytes=size,
        )
        return size

    async def restore(self, source_dir: Path) -> None:
        """Restore the persistence database from *source_dir*.

        Args:
            source_dir: Directory containing the backup file.

        Raises:
            ComponentBackupError: If the backup file is missing or the
                restore mechanism fails.
        """
        source_file = source_dir / self._filename
        if not source_file.exists():
            msg = self._missing_source_message(source_file)
            logger.warning(
                BACKUP_COMPONENT_FAILED,
                component=self.component.value,
                error=msg,
            )
            raise ComponentBackupError(msg)
        await self._do_restore(source_file)

    async def validate_source(self, source_dir: Path) -> bool:
        """Validate that *source_dir* holds a restorable backup.

        Args:
            source_dir: Directory to validate.

        Returns:
            ``True`` if the backup is structurally valid; ``False`` if the
            backup file is missing.

        Raises:
            ComponentBackupError: If validation could not be run.
        """
        source_file = source_dir / self._filename
        if not source_file.exists():
            return False
        return await self._do_validate(source_file)

    @abstractmethod
    def _started_log_fields(self) -> dict[str, object]:
        """Return backend-specific fields for the STARTED log event."""

    @abstractmethod
    def _missing_source_message(self, source_file: Path) -> str:
        """Return the error message for a missing restore source file.

        Args:
            source_file: The path that was expected to exist.
        """

    @abstractmethod
    async def _do_backup(self, target_file: Path) -> int:
        """Run the backend-specific backup mechanism.

        Args:
            target_file: Full path to write the backup into.

        Returns:
            Size of the backup file in bytes.
        """

    @abstractmethod
    async def _do_restore(self, source_file: Path) -> None:
        """Run the backend-specific restore mechanism.

        Args:
            source_file: Full path of the backup file to restore.
        """

    @abstractmethod
    async def _do_validate(self, source_file: Path) -> bool:
        """Run the backend-specific validation mechanism.

        Args:
            source_file: Full path of the backup file to validate.

        Returns:
            ``True`` when the backup is structurally valid.
        """
