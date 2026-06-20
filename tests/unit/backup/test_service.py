"""Tests for BackupService -- central orchestrator for backup/restore."""

from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.backup.config import BackupConfig, RetentionConfig
from synthorg.backup.errors import BackupInProgressError, BackupNotFoundError
from synthorg.backup.handlers.protocol import ComponentHandler
from synthorg.backup.models import (
    BackupComponent,
    BackupManifest,
    BackupTrigger,
)
from synthorg.backup.service import BackupService


def _make_handler(component: BackupComponent) -> ComponentHandler:
    """Build a mock ComponentHandler for testing.

    A hand-assembled ``MagicMock`` (not ``mock_of`` / autospec) because
    ``BackupService.__init__`` deep-copies its handlers dict and an
    autospec'd ``runtime_checkable`` Protocol instance is not
    deep-copyable (its state carries the un-picklable ABC caches).
    """
    handler = MagicMock()
    handler.component = component
    handler.backup = AsyncMock(return_value=512)
    handler.restore = AsyncMock()
    handler.validate_source = AsyncMock(return_value=True)
    return cast("ComponentHandler", handler)


def _make_service(
    backup_path: Path,
    *,
    enabled: bool = True,
    compression: bool = False,
    handlers: dict[BackupComponent, ComponentHandler] | None = None,
    schedule_hours: int = 6,
) -> BackupService:
    """Build a BackupService with tmp_path-based config and mock handlers."""
    config = BackupConfig(
        enabled=enabled,
        path=str(backup_path),
        schedule_hours=schedule_hours,
        compression=compression,
        include=(
            BackupComponent.PERSISTENCE,
            BackupComponent.MEMORY,
            BackupComponent.CONFIG,
        ),
        retention=RetentionConfig(max_count=10, max_age_days=30),
    )
    if handlers is None:
        handlers = {
            BackupComponent.PERSISTENCE: _make_handler(BackupComponent.PERSISTENCE),
            BackupComponent.MEMORY: _make_handler(BackupComponent.MEMORY),
            BackupComponent.CONFIG: _make_handler(BackupComponent.CONFIG),
        }
    return BackupService(config, handlers)


_EMPTY_DIR_CHECKSUM = (
    "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)


def _create_backup_on_disk(
    backup_path: Path,
    backup_id: str,
    trigger: BackupTrigger,
    timestamp: str,
) -> Path:
    """Create a fake backup directory with a manifest.json for lookups."""
    dir_name = f"{backup_id}_{trigger.value}"
    backup_dir = backup_path / dir_name
    backup_dir.mkdir(parents=True, exist_ok=True)
    manifest = BackupManifest(
        synthorg_version="0.3.2",
        timestamp=timestamp,
        trigger=trigger,
        components=(BackupComponent.PERSISTENCE,),
        size_bytes=100,
        checksum=_EMPTY_DIR_CHECKSUM,
        backup_id=backup_id,
    )
    (backup_dir / "manifest.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return backup_dir


# ---------------------------------------------------------------------------
# create_backup
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateBackup:
    """Tests for create_backup()."""

    async def test_creates_directory_calls_handlers_writes_manifest(
        self,
        tmp_path: Path,
    ) -> None:
        """create_backup() creates a backup dir, invokes handlers, writes manifest."""
        bp = tmp_path / "backups"
        service = _make_service(bp)

        manifest = await service.create_backup(BackupTrigger.MANUAL)

        assert manifest.trigger == BackupTrigger.MANUAL
        assert manifest.backup_id
        assert manifest.size_bytes == 1536  # 3 handlers x 512 each
        assert manifest.checksum.startswith("sha256:")

        # Handler backup methods were called
        for handler in service._handlers.values():
            handler.backup.assert_called_once()  # type: ignore[attr-defined]

    async def test_raises_backup_in_progress_error_when_locked(
        self,
        tmp_path: Path,
    ) -> None:
        """create_backup() raises BackupInProgressError if lock is held."""
        bp = tmp_path / "backups"
        service = _make_service(bp)

        # Acquire the lock externally to simulate in-progress backup
        await service._backup_lock.acquire()
        try:
            with pytest.raises(
                BackupInProgressError,
                match="backup is already in progress",
            ):
                await service.create_backup(BackupTrigger.MANUAL)
        finally:
            service._backup_lock.release()

    async def test_creates_compressed_archive(
        self,
        tmp_path: Path,
    ) -> None:
        """create_backup() with compression creates a tar.gz and removes dir."""
        bp = tmp_path / "backups"
        service = _make_service(bp, compression=True)

        manifest = await service.create_backup(
            BackupTrigger.MANUAL,
            compress=True,
        )

        # The uncompressed directory should be removed
        dir_name = f"{manifest.backup_id}_{BackupTrigger.MANUAL.value}"
        assert not (bp / dir_name).exists()
        # The archive should exist
        assert (bp / f"{dir_name}.tar.gz").exists()

    async def test_shutdown_trigger_skips_compression(
        self,
        tmp_path: Path,
    ) -> None:
        """Shutdown backups skip compression for speed even when configured."""
        bp = tmp_path / "backups"
        service = _make_service(bp, compression=True)

        manifest = await service.create_backup(BackupTrigger.SHUTDOWN)

        # Uncompressed directory should exist (no tar.gz)
        dir_name = f"{manifest.backup_id}_{BackupTrigger.SHUTDOWN.value}"
        assert (bp / dir_name).is_dir()

    async def test_explicit_compress_false_overrides_config(
        self,
        tmp_path: Path,
    ) -> None:
        """compress=False overrides config.compression=True."""
        bp = tmp_path / "backups"
        service = _make_service(bp, compression=True)

        manifest = await service.create_backup(
            BackupTrigger.MANUAL,
            compress=False,
        )

        dir_name = f"{manifest.backup_id}_{BackupTrigger.MANUAL.value}"
        assert (bp / dir_name).is_dir()

    async def test_selective_components(
        self,
        tmp_path: Path,
    ) -> None:
        """Only specified components are backed up."""
        bp = tmp_path / "backups"
        service = _make_service(bp)

        await service.create_backup(
            BackupTrigger.MANUAL,
            components=(BackupComponent.PERSISTENCE,),
        )

        # Verify manifest only includes the requested component
        backups = await service.list_backups()
        assert len(backups) == 1
        assert backups[0].components == (BackupComponent.PERSISTENCE,)


# ---------------------------------------------------------------------------
# list_backups
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListBackups:
    """Tests for list_backups()."""

    async def test_returns_sorted_by_timestamp_descending(
        self,
        tmp_path: Path,
    ) -> None:
        """list_backups() returns info sorted newest-first."""
        bp = tmp_path / "backups"
        bp.mkdir()

        ts1 = "2026-03-01T00:00:00Z"
        ts2 = "2026-03-18T00:00:00Z"
        ts3 = "2026-03-10T00:00:00Z"
        _create_backup_on_disk(bp, "aabb00000001", BackupTrigger.MANUAL, ts1)
        _create_backup_on_disk(bp, "aabb00000003", BackupTrigger.MANUAL, ts2)
        _create_backup_on_disk(bp, "aabb00000002", BackupTrigger.MANUAL, ts3)

        service = _make_service(bp)
        infos = await service.list_backups()

        assert len(infos) == 3
        assert infos[0].backup_id == "aabb00000003"
        assert infos[1].backup_id == "aabb00000002"
        assert infos[2].backup_id == "aabb00000001"

    async def test_empty_when_no_backups(
        self,
        tmp_path: Path,
    ) -> None:
        """list_backups() returns empty tuple when no backups exist."""
        bp = tmp_path / "backups"
        bp.mkdir()
        service = _make_service(bp)

        infos = await service.list_backups()
        assert infos == ()

    async def test_empty_when_directory_missing(
        self,
        tmp_path: Path,
    ) -> None:
        """list_backups() returns empty tuple when backup dir does not exist."""
        bp = tmp_path / "no-such-dir"
        service = _make_service(bp)

        infos = await service.list_backups()
        assert infos == ()


# ---------------------------------------------------------------------------
# get_backup
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetBackup:
    """Tests for get_backup()."""

    async def test_returns_manifest_for_existing_backup(
        self,
        tmp_path: Path,
    ) -> None:
        """get_backup() returns full manifest when backup exists."""
        bp = tmp_path / "backups"
        bp.mkdir()
        ts = "2026-03-18T00:00:00Z"
        _create_backup_on_disk(bp, "aabb00000001", BackupTrigger.MANUAL, ts)

        service = _make_service(bp)
        manifest = await service.get_backup("aabb00000001")

        assert manifest.backup_id == "aabb00000001"
        assert manifest.trigger == BackupTrigger.MANUAL

    async def test_raises_not_found_for_missing_backup(
        self,
        tmp_path: Path,
    ) -> None:
        """get_backup() raises BackupNotFoundError for nonexistent ID."""
        bp = tmp_path / "backups"
        bp.mkdir()
        service = _make_service(bp)

        with pytest.raises(BackupNotFoundError, match="not found"):
            await service.get_backup("aabb00000099")


# ---------------------------------------------------------------------------
# delete_backup
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteBackup:
    """Tests for delete_backup()."""

    async def test_removes_backup_directory(
        self,
        tmp_path: Path,
    ) -> None:
        """delete_backup() removes the backup directory from disk."""
        bp = tmp_path / "backups"
        bp.mkdir()
        backup_dir = _create_backup_on_disk(
            bp, "aabb00000001", BackupTrigger.MANUAL, "2026-03-18T00:00:00Z"
        )
        assert backup_dir.exists()

        service = _make_service(bp)
        await service.delete_backup("aabb00000001")

        assert not backup_dir.exists()

    async def test_raises_not_found_for_missing_backup(
        self,
        tmp_path: Path,
    ) -> None:
        """delete_backup() raises BackupNotFoundError for nonexistent ID."""
        bp = tmp_path / "backups"
        bp.mkdir()
        service = _make_service(bp)

        with pytest.raises(BackupNotFoundError, match="not found"):
            await service.delete_backup("aabb00000099")


# ---------------------------------------------------------------------------
# restore_from_backup
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRestoreFromBackup:
    """Tests for restore_from_backup()."""

    async def test_creates_safety_backup_and_restores(
        self,
        tmp_path: Path,
    ) -> None:
        """restore_from_backup() creates a safety backup then restores data."""
        bp = tmp_path / "backups"
        bp.mkdir()
        handlers = {
            BackupComponent.PERSISTENCE: _make_handler(BackupComponent.PERSISTENCE),
        }
        _create_backup_on_disk(
            bp, "aabb00000001", BackupTrigger.MANUAL, "2026-03-18T00:00:00Z"
        )

        service = _make_service(bp, handlers=handlers)
        response = await service.restore_from_backup("aabb00000001")

        # Safety backup was created with PRE_MIGRATION trigger
        assert response.safety_backup_id
        assert response.manifest.backup_id == "aabb00000001"

        # Verify restore completed successfully
        assert response.restored_components == (BackupComponent.PERSISTENCE,)
        assert response.restart_required is True

    async def test_raises_not_found_for_missing_source(
        self,
        tmp_path: Path,
    ) -> None:
        """restore_from_backup() raises BackupNotFoundError for missing backup."""
        bp = tmp_path / "backups"
        bp.mkdir()
        service = _make_service(bp)

        with pytest.raises(BackupNotFoundError, match="not found"):
            await service.restore_from_backup("aabb00000099")


# ---------------------------------------------------------------------------
# start / stop (scheduler delegation)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestServiceLifecycle:
    """Tests for start() and stop() scheduler delegation."""

    async def test_start_starts_scheduler_when_enabled(
        self,
        tmp_path: Path,
    ) -> None:
        """start() starts the backup scheduler when backups are enabled."""
        bp = tmp_path / "backups"
        service = _make_service(bp, enabled=True)

        with patch.object(service._scheduler, "start") as mock_start:
            await service.start()
            mock_start.assert_called_once()

    async def test_start_skips_scheduler_when_disabled(
        self,
        tmp_path: Path,
    ) -> None:
        """start() does not start scheduler when backups are disabled."""
        bp = tmp_path / "backups"
        service = _make_service(bp, enabled=False)

        with patch.object(service._scheduler, "start") as mock_start:
            await service.start()
            mock_start.assert_not_called()

    async def test_stop_stops_scheduler(
        self,
        tmp_path: Path,
    ) -> None:
        """stop() delegates to scheduler.stop()."""
        bp = tmp_path / "backups"
        service = _make_service(bp)

        with patch.object(
            service._scheduler, "stop", new_callable=AsyncMock
        ) as mock_stop:
            await service.stop()
            mock_stop.assert_called_once()

    def test_is_unrestartable_delegates_to_scheduler(
        self,
        tmp_path: Path,
    ) -> None:
        """is_unrestartable surfaces the scheduler's timed-out-stop flag."""
        bp = tmp_path / "backups"
        service = _make_service(bp)

        assert service.is_unrestartable is False
        # A timed-out scheduler stop sets the private flag; the service
        # property must reflect it without the caller catching the
        # BackupUnrestartableError raised by start().
        service._scheduler._stop_failed = True
        assert service.is_unrestartable is True
