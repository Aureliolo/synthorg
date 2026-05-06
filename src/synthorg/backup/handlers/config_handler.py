"""Config component handler -- single-file copy of company config."""

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

_CONFIG_SUBDIR = "config"


class ConfigComponentHandler:
    """Back up and restore the company YAML configuration file.

    Uses ``shutil.copy2`` for single-file copies (backup and restore).

    Args:
        config_path: Path to the company configuration YAML file.
    """

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path

    @property
    def component(self) -> BackupComponent:
        """Return the component this handler manages."""
        return BackupComponent.CONFIG

    async def backup(self, target_dir: Path) -> int:
        """Copy the configuration file to the backup target.

        Args:
            target_dir: Directory to write the backup into.

        Returns:
            Size of the copied file in bytes.

        Raises:
            ComponentBackupError: If the backup operation fails.
        """
        logger.info(
            BACKUP_COMPONENT_STARTED,
            component=self.component.value,
            config_path=str(self._config_path),
        )
        if not self._config_path.exists():
            logger.info(
                BACKUP_COMPONENT_COMPLETED,
                component=self.component.value,
                size_bytes=0,
                note="config file does not exist, skipping",
            )
            return 0

        config_dir = target_dir / _CONFIG_SUBDIR
        try:
            size = await asyncio.to_thread(
                self._copy_config,
                self._config_path,
                config_dir,
            )
        except Exception as exc:
            logger.error(
                BACKUP_COMPONENT_FAILED,
                component=self.component.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to back up config file: {exc}"
            raise ComponentBackupError(msg) from exc
        logger.info(
            BACKUP_COMPONENT_COMPLETED,
            component=self.component.value,
            size_bytes=size,
        )
        return size

    async def restore(self, source_dir: Path) -> None:
        """Restore the configuration file from a backup.

        Looks up the original config filename rather than relying
        on directory listing order.

        Args:
            source_dir: Directory containing the backup config.

        Raises:
            ComponentBackupError: If restore fails.
        """
        config_dir = source_dir / _CONFIG_SUBDIR
        exists = await asyncio.to_thread(config_dir.exists)
        if not exists:
            logger.warning(
                BACKUP_COMPONENT_FAILED,
                component=self.component.value,
                error=f"Backup config directory not found: {config_dir}",
            )
            msg = f"Backup config directory not found: {config_dir}"
            raise ComponentBackupError(msg)

        # Look for the original filename first, fall back to first file
        source_file = config_dir / self._config_path.name
        if not source_file.exists():
            backup_files = list(config_dir.iterdir())
            if not backup_files:
                logger.warning(
                    BACKUP_COMPONENT_FAILED,
                    component=self.component.value,
                    error=f"No config files found in backup: {config_dir}",
                )
                msg = f"No config files found in backup: {config_dir}"
                raise ComponentBackupError(msg)
            source_file = backup_files[0]

        try:
            await asyncio.to_thread(
                shutil.copy2,
                source_file,
                self._config_path,
            )
        except Exception as exc:
            logger.error(
                BACKUP_COMPONENT_FAILED,
                component=self.component.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to restore config file: {exc}"
            raise ComponentBackupError(msg) from exc

    async def validate_source(self, source_dir: Path) -> bool:
        """Validate that backup config directory contains files.

        Args:
            source_dir: Directory to validate.

        Returns:
            ``True`` if the config backup subdirectory exists and
            contains at least one file.
        """
        return await asyncio.to_thread(self._check_source, source_dir)

    @staticmethod
    def _check_source(source_dir: Path) -> bool:
        """Synchronous check for config backup validity."""
        config_dir = source_dir / _CONFIG_SUBDIR
        if not config_dir.exists():
            return False
        return any(config_dir.iterdir())

    @staticmethod
    def _copy_config(config_path: Path, target_dir: Path) -> int:
        """Copy config file and return bytes written."""
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / config_path.name
        shutil.copy2(config_path, dest)
        return dest.stat().st_size
