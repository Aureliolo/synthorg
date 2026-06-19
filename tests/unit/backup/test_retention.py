"""Tests for RetentionManager -- prune old backups by count and age."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from synthorg.backup.config import RetentionConfig
from synthorg.backup.errors import RetentionError
from synthorg.backup.models import BackupComponent, BackupManifest, BackupTrigger
from synthorg.backup.retention import RetentionManager


def _make_manifest(
    backup_id: str,
    trigger: BackupTrigger,
    timestamp: str,
) -> dict[str, object]:
    """Build a raw manifest dict for writing to disk."""
    return BackupManifest(
        synthorg_version="0.3.2",
        timestamp=timestamp,
        trigger=trigger,
        components=(BackupComponent.PERSISTENCE,),
        size_bytes=100,
        checksum="sha256:" + "a" * 64,
        backup_id=backup_id,
    ).model_dump()


def _create_backup_dir(
    backup_path: Path,
    backup_id: str,
    trigger: BackupTrigger,
    timestamp: str,
) -> Path:
    """Create a fake backup directory with a manifest.json file."""
    dir_name = f"{backup_id}_{trigger.value}"
    backup_dir = backup_path / dir_name
    backup_dir.mkdir(parents=True, exist_ok=True)
    manifest = _make_manifest(backup_id, trigger, timestamp)
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return backup_dir


@pytest.mark.unit
class TestRetentionPruneEmpty:
    """Pruning when there are no backups."""

    async def test_empty_directory_returns_empty_tuple(
        self,
        backup_path: Path,
        retention_config: RetentionConfig,
    ) -> None:
        """prune() returns empty tuple when backup directory is empty."""
        manager = RetentionManager(retention_config, backup_path)
        result = await manager.prune()
        assert result == ()

    async def test_nonexistent_directory_returns_empty_tuple(
        self,
        tmp_path: Path,
        retention_config: RetentionConfig,
    ) -> None:
        """prune() returns empty tuple when backup directory does not exist."""
        missing = tmp_path / "does-not-exist"
        manager = RetentionManager(retention_config, missing)
        result = await manager.prune()
        assert result == ()


@pytest.mark.unit
class TestRetentionPruneByCount:
    """Pruning based on max_count policy."""

    async def test_removes_backups_exceeding_max_count(
        self,
        backup_path: Path,
    ) -> None:
        """Backups beyond max_count are pruned (oldest first)."""
        config = RetentionConfig(max_count=2, max_age_days=365)
        now = datetime.now(UTC)

        # Create 4 backups -- newest to oldest
        for i in range(4):
            ts = (now - timedelta(hours=i)).isoformat()
            _create_backup_dir(backup_path, f"bk{i:03d}", BackupTrigger.MANUAL, ts)

        manager = RetentionManager(config, backup_path)
        pruned = await manager.prune()

        # bk000 is newest (i=0), bk001 is second (i=1, within max_count=2)
        # bk002 (i=2, index 2 >= max_count 2) and bk003 (i=3) should be pruned
        assert len(pruned) == 2
        assert "bk002" in pruned
        assert "bk003" in pruned

    async def test_single_backup_never_pruned(
        self,
        backup_path: Path,
    ) -> None:
        """A single backup is never pruned even with max_count=1."""
        config = RetentionConfig(max_count=1, max_age_days=365)
        ts = datetime.now(UTC).isoformat()
        _create_backup_dir(backup_path, "only-one", BackupTrigger.MANUAL, ts)

        manager = RetentionManager(config, backup_path)
        pruned = await manager.prune()
        assert pruned == ()


@pytest.mark.unit
class TestRetentionPruneByAge:
    """Pruning based on max_age_days policy."""

    async def test_removes_backups_older_than_max_age(
        self,
        backup_path: Path,
    ) -> None:
        """Backups older than max_age_days are pruned."""
        config = RetentionConfig(max_count=100, max_age_days=7)
        now = datetime.now(UTC)

        # Recent backup (1 day old)
        ts_recent = (now - timedelta(days=1)).isoformat()
        _create_backup_dir(backup_path, "recent", BackupTrigger.MANUAL, ts_recent)

        # Old backup (10 days old -- exceeds 7-day max_age)
        ts_old = (now - timedelta(days=10)).isoformat()
        _create_backup_dir(backup_path, "old-one", BackupTrigger.MANUAL, ts_old)

        manager = RetentionManager(config, backup_path)
        pruned = await manager.prune()

        assert "old-one" in pruned
        assert "recent" not in pruned

    async def test_within_age_not_pruned(
        self,
        backup_path: Path,
    ) -> None:
        """A backup within the age limit is not pruned."""
        config = RetentionConfig(max_count=100, max_age_days=7)
        now = datetime.now(UTC)

        # Newest backup
        ts_newest = now.isoformat()
        _create_backup_dir(backup_path, "newest", BackupTrigger.MANUAL, ts_newest)

        # Backup 5 days old -- safely within 7-day max_age
        ts_recent = (now - timedelta(days=5)).isoformat()
        _create_backup_dir(backup_path, "recent", BackupTrigger.MANUAL, ts_recent)

        manager = RetentionManager(config, backup_path)
        pruned = await manager.prune()

        assert "recent" not in pruned


@pytest.mark.unit
class TestRetentionResolverDrivenAge:
    """The live ``backup.retention_days`` setting overrides static config."""

    async def test_resolver_value_overrides_static_max_age(
        self,
        backup_path: Path,
    ) -> None:
        """A resolver-supplied retention_days wins over config.max_age_days."""
        from unittest.mock import AsyncMock

        from synthorg.settings.resolver_protocol import ConfigResolverProtocol

        # Static config would keep everything (365 days); the resolver
        # narrows the age cap to 7 days.
        config = RetentionConfig(max_count=100, max_age_days=365)
        resolver = AsyncMock(spec=ConfigResolverProtocol)
        resolver.get_int.return_value = 7
        now = datetime.now(UTC)

        ts_recent = (now - timedelta(days=1)).isoformat()
        _create_backup_dir(backup_path, "recent", BackupTrigger.MANUAL, ts_recent)
        ts_old = (now - timedelta(days=10)).isoformat()
        _create_backup_dir(backup_path, "old-one", BackupTrigger.MANUAL, ts_old)

        manager = RetentionManager(config, backup_path, config_resolver=resolver)
        pruned = await manager.prune()

        assert "old-one" in pruned
        assert "recent" not in pruned

    async def test_resolver_failure_falls_back_to_static(
        self,
        backup_path: Path,
    ) -> None:
        """A resolver outage falls back to the static max_age_days."""
        from unittest.mock import AsyncMock

        from synthorg.settings.resolver_protocol import ConfigResolverProtocol

        config = RetentionConfig(max_count=100, max_age_days=7)
        resolver = AsyncMock(spec=ConfigResolverProtocol)
        resolver.get_int.side_effect = RuntimeError("settings backend down")
        now = datetime.now(UTC)

        ts_recent = (now - timedelta(days=1)).isoformat()
        _create_backup_dir(backup_path, "recent", BackupTrigger.MANUAL, ts_recent)
        ts_old = (now - timedelta(days=10)).isoformat()
        _create_backup_dir(backup_path, "old-one", BackupTrigger.MANUAL, ts_old)

        manager = RetentionManager(config, backup_path, config_resolver=resolver)
        pruned = await manager.prune()

        assert "old-one" in pruned
        assert "recent" not in pruned


@pytest.mark.unit
class TestRetentionNewestProtection:
    """The most recent backup is never pruned."""

    async def test_newest_backup_never_pruned_even_if_over_count(
        self,
        backup_path: Path,
    ) -> None:
        """The newest backup is protected regardless of count policy."""
        config = RetentionConfig(max_count=1, max_age_days=365)
        now = datetime.now(UTC)

        ts_new = now.isoformat()
        ts_old = (now - timedelta(days=1)).isoformat()

        _create_backup_dir(backup_path, "newest", BackupTrigger.MANUAL, ts_new)
        _create_backup_dir(backup_path, "oldest", BackupTrigger.MANUAL, ts_old)

        manager = RetentionManager(config, backup_path)
        pruned = await manager.prune()

        assert "newest" not in pruned
        assert "oldest" in pruned


@pytest.mark.unit
class TestRetentionPreMigrationProtection:
    """pre_migration backups are never pruned."""

    async def test_pre_migration_backup_never_pruned(
        self,
        backup_path: Path,
    ) -> None:
        """A pre_migration backup is protected even if it exceeds policies."""
        config = RetentionConfig(max_count=1, max_age_days=1)
        now = datetime.now(UTC)

        # Newest manual backup
        ts_new = now.isoformat()
        _create_backup_dir(backup_path, "newest", BackupTrigger.MANUAL, ts_new)

        # Old pre_migration backup (exceeds both count and age)
        ts_old = (now - timedelta(days=30)).isoformat()
        _create_backup_dir(backup_path, "safety", BackupTrigger.PRE_MIGRATION, ts_old)

        # Old manual backup (should be pruned)
        ts_old2 = (now - timedelta(days=20)).isoformat()
        _create_backup_dir(backup_path, "old-manual", BackupTrigger.MANUAL, ts_old2)

        manager = RetentionManager(config, backup_path)
        pruned = await manager.prune()

        assert "safety" not in pruned
        assert "old-manual" in pruned

    async def test_multiple_pre_migration_backups_all_protected(
        self,
        backup_path: Path,
    ) -> None:
        """All pre_migration backups are retained regardless of policies."""
        config = RetentionConfig(max_count=1, max_age_days=1)
        now = datetime.now(UTC)

        ts_new = now.isoformat()
        _create_backup_dir(backup_path, "newest", BackupTrigger.MANUAL, ts_new)

        for i in range(3):
            ts = (now - timedelta(days=10 + i)).isoformat()
            _create_backup_dir(backup_path, f"pm{i}", BackupTrigger.PRE_MIGRATION, ts)

        manager = RetentionManager(config, backup_path)
        pruned = await manager.prune()

        for i in range(3):
            assert f"pm{i}" not in pruned


@pytest.mark.unit
class TestRetentionPruneErrors:
    """Error handling in prune()."""

    async def test_corrupt_manifest_skipped(
        self,
        backup_path: Path,
        retention_config: RetentionConfig,
    ) -> None:
        """A corrupt manifest.json is silently skipped (not loaded)."""
        # Create a valid backup
        now = datetime.now(UTC)
        _create_backup_dir(backup_path, "valid", BackupTrigger.MANUAL, now.isoformat())

        # Create a directory with corrupt manifest
        corrupt_dir = backup_path / "corrupt_manual"
        corrupt_dir.mkdir()
        (corrupt_dir / "manifest.json").write_text(
            "not valid json!!!",
            encoding="utf-8",
        )

        manager = RetentionManager(retention_config, backup_path)
        # Should not raise -- corrupt manifest is skipped
        result = await manager.prune()
        assert result == ()

    async def test_prune_raises_retention_error_on_load_failure(
        self,
        tmp_path: Path,
        retention_config: RetentionConfig,
    ) -> None:
        """prune() wraps unexpected load errors in RetentionError."""
        # Create a backup_path that is a file, not a directory,
        # so iterdir() will fail
        bad_path = tmp_path / "backups"
        bad_path.write_text("not a directory", encoding="utf-8")

        manager = RetentionManager(retention_config, bad_path)
        with pytest.raises(RetentionError, match="Failed to load manifests"):
            await manager.prune()
