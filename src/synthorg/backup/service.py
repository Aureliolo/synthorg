"""Backup service -- central orchestrator for backup/restore operations."""

import asyncio
import re
import shutil
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import uuid4

from synthorg import __version__
from synthorg.backup.errors import (
    BackupInProgressError,
    BackupNotFoundError,
    ManifestError,
    RestoreError,
)
from synthorg.backup.models import (
    BackupComponent,
    BackupManifest,
    BackupTrigger,
    RestoreResponse,
)
from synthorg.backup.retention import RetentionManager
from synthorg.backup.scheduler import BackupScheduler
from synthorg.backup.service_archive import BackupServiceArchiveMixin
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.backup import (
    BACKUP_COMPLETED,
    BACKUP_FAILED,
    BACKUP_IN_PROGRESS,
    BACKUP_MANIFEST_WRITTEN,
    BACKUP_RESTORE_COMPLETED,
    BACKUP_RESTORE_FAILED,
    BACKUP_RESTORE_ROLLBACK,
    BACKUP_RESTORE_STARTED,
    BACKUP_RETENTION_FAILED,
    BACKUP_STARTED,
)

if TYPE_CHECKING:
    from synthorg.backup.config import BackupConfig
    from synthorg.backup.handlers.protocol import ComponentHandler

logger = get_logger(__name__)

_BACKUP_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def _validate_backup_id(backup_id: str) -> None:
    """Validate backup_id format at service boundary."""
    if not _BACKUP_ID_RE.match(backup_id):
        msg = (
            f"Invalid backup_id format: {backup_id!r}. "
            "Expected 12-character hex string."
        )
        raise BackupNotFoundError(msg)


class BackupService(BackupServiceArchiveMixin):
    """Central orchestrator for backup and restore operations."""

    def __init__(
        self,
        config: BackupConfig,
        handlers: dict[BackupComponent, ComponentHandler],
    ) -> None:
        self._config = config
        self._handlers: MappingProxyType[BackupComponent, ComponentHandler] = (
            MappingProxyType(deepcopy(handlers))
        )
        self._backup_lock = asyncio.Lock()
        self._backup_path = Path(config.path)
        self._retention = RetentionManager(config.retention, self._backup_path)
        self._scheduler = BackupScheduler(self, config.schedule_hours)

    @property
    def scheduler(self) -> BackupScheduler:
        """Return the backup scheduler instance."""
        return self._scheduler

    @property
    def on_startup(self) -> bool:
        """Whether to create a backup on application startup."""
        return self._config.on_startup

    @property
    def on_shutdown(self) -> bool:
        """Whether to create a backup on graceful shutdown."""
        return self._config.on_shutdown

    async def start(self) -> None:
        """Start the backup scheduler if backups are enabled."""
        if self._config.enabled:
            self._scheduler.start()

    async def stop(self) -> None:
        """Stop the backup scheduler."""
        await self._scheduler.stop()

    async def create_backup(
        self,
        trigger: BackupTrigger,
        components: tuple[BackupComponent, ...] | None = None,
        *,
        compress: bool | None = None,
    ) -> BackupManifest:
        """Create a new backup."""
        if self._backup_lock.locked():
            logger.warning(BACKUP_IN_PROGRESS, trigger=trigger.value)
            msg = "A backup is already in progress"
            raise BackupInProgressError(msg)

        async with self._backup_lock:
            return await self._do_backup(trigger, components, compress=compress)

    async def _do_backup(
        self,
        trigger: BackupTrigger,
        components: tuple[BackupComponent, ...] | None = None,
        *,
        compress: bool | None = None,
    ) -> BackupManifest:
        """Execute the backup. Caller must hold ``_backup_lock``."""
        backup_id = uuid4().hex[:12]
        timestamp = datetime.now(UTC).isoformat()
        effective_components = components or self._config.include

        if compress is None:
            use_compression = (
                self._config.compression if trigger != BackupTrigger.SHUTDOWN else False
            )
        else:
            use_compression = compress

        dir_name = f"{backup_id}_{trigger.value}"
        backup_dir = self._backup_path / dir_name

        logger.info(
            BACKUP_STARTED,
            backup_id=backup_id,
            trigger=trigger.value,
            components=[c.value for c in effective_components],
        )

        try:
            manifest = await self._execute_backup(
                backup_id=backup_id,
                timestamp=timestamp,
                trigger=trigger,
                effective_components=effective_components,
                use_compression=use_compression,
                dir_name=dir_name,
                backup_dir=backup_dir,
            )
        except Exception as exc:
            logger.error(  # noqa: TRY400
                BACKUP_FAILED,
                backup_id=backup_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            if backup_dir.exists():
                await asyncio.to_thread(shutil.rmtree, backup_dir)
            raise
        return manifest

    async def _execute_backup(  # noqa: PLR0913
        self,
        *,
        backup_id: str,
        timestamp: str,
        trigger: BackupTrigger,
        effective_components: tuple[BackupComponent, ...],
        use_compression: bool,
        dir_name: str,
        backup_dir: Path,
    ) -> BackupManifest:
        """Run the backup steps: create dirs, copy data, write manifest."""
        await asyncio.to_thread(self._backup_path.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(backup_dir.mkdir, parents=True, exist_ok=True)

        backed_up_components: list[BackupComponent] = []
        total_size = 0
        for comp in effective_components:
            handler = self._handlers.get(comp)
            if handler is None:
                logger.warning(
                    BACKUP_FAILED,
                    backup_id=backup_id,
                    component=comp.value,
                    error="No handler registered",
                )
                continue
            size = await handler.backup(backup_dir)
            total_size += size
            backed_up_components.append(comp)

        checksum = await asyncio.to_thread(
            self._compute_checksum,
            backup_dir,
        )

        manifest = BackupManifest(
            synthorg_version=__version__,
            timestamp=timestamp,
            trigger=trigger,
            components=tuple(backed_up_components),
            size_bytes=total_size,
            checksum=f"sha256:{checksum}",
            backup_id=backup_id,
        )

        manifest_path = backup_dir / "manifest.json"
        await asyncio.to_thread(
            manifest_path.write_text,
            manifest.model_dump_json(indent=2),
            "utf-8",
        )
        logger.debug(
            BACKUP_MANIFEST_WRITTEN,
            backup_id=backup_id,
            path=str(manifest_path),
        )

        await self._finalize_backup(
            backup_id=backup_id,
            trigger=trigger,
            use_compression=use_compression,
            dir_name=dir_name,
            backup_dir=backup_dir,
            total_size=total_size,
        )
        return manifest

    async def _finalize_backup(  # noqa: PLR0913
        self,
        *,
        backup_id: str,
        trigger: BackupTrigger,
        use_compression: bool,
        dir_name: str,
        backup_dir: Path,
        total_size: int,
    ) -> None:
        """Compress and run retention pruning after backup."""
        if use_compression:
            archive_path = self._backup_path / f"{dir_name}.tar.gz"
            await asyncio.to_thread(
                self._compress_dir,
                backup_dir,
                archive_path,
            )
            await asyncio.to_thread(shutil.rmtree, backup_dir)

        logger.info(
            BACKUP_COMPLETED,
            backup_id=backup_id,
            trigger=trigger.value,
            size_bytes=total_size,
            compressed=use_compression,
        )

        try:
            await self._retention.prune()
        except Exception as exc:
            # Drop exc_info on retention failure so the filesystem
            # path / connection details that ``str(exc)`` would
            # carry don't leak via the traceback frame-locals.
            logger.error(  # noqa: TRY400 -- fail-loud, no traceback
                BACKUP_RETENTION_FAILED,
                backup_id=backup_id,
                reason="retention_pruning_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def restore_from_backup(
        self,
        backup_id: str,
        components: tuple[BackupComponent, ...] | None = None,
    ) -> RestoreResponse:
        """Restore data from a backup."""
        _validate_backup_id(backup_id)

        if self._backup_lock.locked():
            logger.warning(BACKUP_IN_PROGRESS, backup_id=backup_id)
            msg = "A backup or restore is already in progress"
            raise BackupInProgressError(msg)

        async with self._backup_lock:
            return await self._do_restore(backup_id, components)

    async def _do_restore(
        self,
        backup_id: str,
        components: tuple[BackupComponent, ...] | None = None,
    ) -> RestoreResponse:
        """Execute the restore (called under lock)."""
        logger.info(BACKUP_RESTORE_STARTED, backup_id=backup_id)

        manifest = await self._load_manifest(backup_id)
        restore_components = components or manifest.components

        backup_dir = await self._find_backup_dir(backup_id)
        temp_extracted = False
        if backup_dir is None:
            backup_dir = await self._extract_archive(backup_id)
            if backup_dir is None:
                msg = f"Backup not found: {backup_id}"
                raise BackupNotFoundError(msg)
            temp_extracted = True

        try:
            await self._verify_checksum(manifest, backup_dir)
            await self._validate_restore_components(restore_components, backup_dir)

            safety_manifest = await self._do_backup(
                BackupTrigger.PRE_MIGRATION,
                components=restore_components,
                compress=False,
            )

            response = await self._perform_component_restore(
                backup_id=backup_id,
                manifest=manifest,
                restore_components=restore_components,
                backup_dir=backup_dir,
                safety_backup_id=safety_manifest.backup_id,
            )
        except RestoreError as exc:
            logger.error(  # noqa: TRY400
                BACKUP_RESTORE_FAILED,
                backup_id=backup_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        finally:
            if temp_extracted and backup_dir is not None:
                exists = await asyncio.to_thread(backup_dir.exists)
                if exists:
                    await asyncio.to_thread(shutil.rmtree, backup_dir)

        return response

    async def _verify_checksum(
        self,
        manifest: BackupManifest,
        backup_dir: Path,
    ) -> None:
        """Re-compute checksum and compare against manifest."""
        computed = await asyncio.to_thread(self._compute_checksum, backup_dir)
        expected = manifest.checksum
        actual = f"sha256:{computed}"
        if actual != expected:
            msg = (
                f"Checksum mismatch for backup {manifest.backup_id}: "
                f"expected {expected}, got {actual}"
            )
            raise ManifestError(msg)

    async def _perform_component_restore(
        self,
        *,
        backup_id: str,
        manifest: BackupManifest,
        restore_components: tuple[BackupComponent, ...],
        backup_dir: Path,
        safety_backup_id: str,
    ) -> RestoreResponse:
        """Restore individual components and build the response."""
        try:
            for comp in restore_components:
                handler = self._handlers.get(comp)
                if handler is None:
                    msg = f"No handler for component: {comp.value}"
                    raise RestoreError(msg)  # noqa: TRY301
                await handler.restore(backup_dir)
        except Exception as exc:
            logger.warning(
                BACKUP_RESTORE_ROLLBACK,
                backup_id=backup_id,
                safety_backup_id=safety_backup_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Restore failed for {backup_id}: {exc}"
            raise RestoreError(msg) from exc

        logger.info(
            BACKUP_RESTORE_COMPLETED,
            backup_id=backup_id,
            components=[c.value for c in restore_components],
            safety_backup_id=safety_backup_id,
        )

        return RestoreResponse(
            manifest=manifest,
            restored_components=restore_components,
            safety_backup_id=safety_backup_id,
        )

    async def _validate_restore_components(
        self,
        restore_components: tuple[BackupComponent, ...],
        backup_dir: Path,
    ) -> None:
        """Validate all restore components have handlers and valid sources."""
        for comp in restore_components:
            handler = self._handlers.get(comp)
            if handler is None:
                logger.warning(
                    BACKUP_RESTORE_FAILED,
                    component=comp.value,
                    reason="no handler",
                )
                msg = f"No handler for component: {comp.value}"
                raise RestoreError(msg)
            valid = await handler.validate_source(backup_dir)
            if not valid:
                logger.warning(
                    BACKUP_RESTORE_FAILED,
                    component=comp.value,
                    reason="invalid backup source",
                )
                msg = f"Invalid backup source for component: {comp.value}"
                raise RestoreError(msg)
