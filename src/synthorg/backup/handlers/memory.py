"""Memory component handler -- copytree-based backup of agent memory."""

import asyncio
import shutil
from typing import TYPE_CHECKING

from synthorg.backup.errors import ComponentBackupError
from synthorg.backup.models import BackupComponent
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.backup import (
    BACKUP_COMPONENT_COMPLETED,
    BACKUP_COMPONENT_FAILED,
    BACKUP_COMPONENT_STARTED,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)

_MEMORY_SUBDIR = "memory"


class MemoryComponentHandler:
    """Back up and restore the agent memory data directory.

    Uses ``shutil.copytree`` with ``symlinks=True`` for
    directory-level copies of the Mem0 data directory
    (Qdrant + history DB).

    Args:
        data_dir: Path to the memory data directory.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir

    @property
    def component(self) -> BackupComponent:
        """Return the component this handler manages."""
        return BackupComponent.MEMORY

    async def backup(self, target_dir: Path) -> int:
        """Copy the memory data directory to the backup target.

        Args:
            target_dir: Directory to write the backup into.

        Returns:
            Total bytes written.

        Raises:
            ComponentBackupError: If the backup operation fails.
        """
        logger.info(
            BACKUP_COMPONENT_STARTED,
            component=self.component.value,
            data_dir=str(self._data_dir),
        )
        exists = await asyncio.to_thread(self._data_dir.exists)
        if not exists:
            logger.info(
                BACKUP_COMPONENT_COMPLETED,
                component=self.component.value,
                size_bytes=0,
                note="source directory does not exist, skipping",
            )
            return 0

        target = target_dir / _MEMORY_SUBDIR
        try:
            size = await asyncio.to_thread(self._copy_tree, self._data_dir, target)
        except Exception as exc:
            logger.error(
                BACKUP_COMPONENT_FAILED,
                component=self.component.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to back up memory data: {exc}"
            raise ComponentBackupError(msg) from exc
        logger.info(
            BACKUP_COMPONENT_COMPLETED,
            component=self.component.value,
            size_bytes=size,
        )
        return size

    async def restore(self, source_dir: Path) -> None:
        """Restore memory data from a backup.

        Performs an atomic swap: renames current directory to
        ``.bak``, copies backup into place.  On failure, renames
        ``.bak`` back.  If the target directory does not exist,
        the backup is copied directly without a swap.

        Args:
            source_dir: Directory containing the backup memory data.

        Raises:
            ComponentBackupError: If restore fails.
        """
        source = source_dir / _MEMORY_SUBDIR
        exists = await asyncio.to_thread(source.exists)
        if not exists:
            logger.warning(
                BACKUP_COMPONENT_FAILED,
                component=self.component.value,
                error=f"Backup memory directory not found: {source}",
            )
            msg = f"Backup memory directory not found: {source}"
            raise ComponentBackupError(msg)

        bak_path = self._data_dir.with_name(f"{self._data_dir.name}.bak")

        try:
            await asyncio.to_thread(
                self._atomic_swap,
                self._data_dir,
                source,
                bak_path,
            )
        except ComponentBackupError:
            raise
        except Exception as exc:
            logger.error(
                BACKUP_COMPONENT_FAILED,
                component=self.component.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to restore memory data: {exc}"
            raise ComponentBackupError(msg) from exc

    async def validate_source(self, source_dir: Path) -> bool:
        """Validate that the backup memory directory exists.

        Args:
            source_dir: Directory to validate.

        Returns:
            ``True`` if the memory backup subdirectory exists.
        """
        memory_path = source_dir / _MEMORY_SUBDIR
        return await asyncio.to_thread(memory_path.exists)

    @staticmethod
    def _copy_tree(source: Path, target: Path) -> int:
        """Copy directory tree and return total bytes copied."""
        shutil.copytree(source, target, symlinks=True)
        total = 0
        for f in target.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
        return total

    @staticmethod
    def _atomic_swap(
        data_path: Path,
        source: Path,
        bak_path: Path,
    ) -> None:
        """Swap the live directory with the backup, rolling back on failure."""
        if data_path.exists():
            shutil.move(data_path, bak_path)

        try:
            shutil.copytree(source, data_path, symlinks=True)
        except Exception:
            if bak_path.exists():
                if data_path.exists():
                    shutil.rmtree(data_path)
                shutil.move(bak_path, data_path)
            raise

        if bak_path.exists():
            shutil.rmtree(bak_path)
