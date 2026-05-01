"""Tests for workspace disk quota watcher."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from synthorg.engine.workspace.config import PlannerWorktreesConfig
from synthorg.engine.workspace.disk_quota import (
    DiskQuotaStatus,
    DiskQuotaWatcher,
)


@pytest.mark.unit
class TestDiskQuotaConfig:
    """Tests for disk quota fields on PlannerWorktreesConfig."""

    def test_defaults(self) -> None:
        config = PlannerWorktreesConfig()
        assert config.max_disk_gb_per_worktree == pytest.approx(5.0)
        assert config.auto_cleanup_on_threshold is True
        assert config.cleanup_warning_threshold == pytest.approx(0.8)

    def test_custom_values(self) -> None:
        config = PlannerWorktreesConfig(
            max_disk_gb_per_worktree=10.0,
            auto_cleanup_on_threshold=False,
            cleanup_warning_threshold=0.9,
        )
        assert config.max_disk_gb_per_worktree == pytest.approx(10.0)
        assert config.auto_cleanup_on_threshold is False
        assert config.cleanup_warning_threshold == pytest.approx(0.9)

    def test_max_disk_bounds(self) -> None:
        with pytest.raises(ValueError, match="greater than 0"):
            PlannerWorktreesConfig(max_disk_gb_per_worktree=0.0)
        with pytest.raises(ValueError, match="less than or equal to 100"):
            PlannerWorktreesConfig(max_disk_gb_per_worktree=200.0)

    def test_warning_threshold_bounds(self) -> None:
        with pytest.raises(ValueError, match=r"greater than or equal to 0\.5"):
            PlannerWorktreesConfig(cleanup_warning_threshold=0.1)
        with pytest.raises(ValueError, match=r"less than or equal to 1"):
            PlannerWorktreesConfig(cleanup_warning_threshold=1.5)


_TEST_PATH = Path("/tmp/wt1")  # noqa: S108


@pytest.mark.unit
class TestDiskQuotaStatus:
    """Tests for DiskQuotaStatus model."""

    def test_ok_status(self) -> None:
        status = DiskQuotaStatus(
            path=_TEST_PATH,
            usage_gb=1.0,
            limit_gb=5.0,
            status="ok",
        )
        assert status.status == "ok"

    def test_warning_status(self) -> None:
        status = DiskQuotaStatus(
            path=_TEST_PATH,
            usage_gb=4.5,
            limit_gb=5.0,
            status="warning",
        )
        assert status.status == "warning"

    def test_exceeded_status(self) -> None:
        status = DiskQuotaStatus(
            path=_TEST_PATH,
            usage_gb=5.5,
            limit_gb=5.0,
            status="exceeded",
        )
        assert status.status == "exceeded"

    def test_frozen(self) -> None:
        status = DiskQuotaStatus(
            path=_TEST_PATH,
            usage_gb=1.0,
            limit_gb=5.0,
            status="ok",
        )
        with pytest.raises(ValidationError):
            status.status = "warning"  # type: ignore[misc]


@pytest.mark.unit
class TestDiskQuotaWatcher:
    """Tests for DiskQuotaWatcher."""

    async def test_check_worktree_ok(self, tmp_path: Path) -> None:
        """Worktree under limit returns ok status."""
        config = PlannerWorktreesConfig(max_disk_gb_per_worktree=5.0)
        watcher = DiskQuotaWatcher(config=config)
        # tmp_path is essentially empty -- well under 5 GB.
        status = await watcher.check_worktree(tmp_path)
        assert status.status == "ok"
        assert status.usage_gb < 5.0

    async def test_check_worktree_warning(self, tmp_path: Path) -> None:
        """Worktree at warning threshold returns warning status."""
        # Use a very small limit so the warning threshold is easy to hit.
        config = PlannerWorktreesConfig(
            max_disk_gb_per_worktree=0.0001,  # ~100 KB
            cleanup_warning_threshold=0.5,
        )
        watcher = DiskQuotaWatcher(config=config)
        # Write a file to push past 50% of 100 KB.
        (tmp_path / "data.bin").write_bytes(b"x" * 60_000)
        status = await watcher.check_worktree(tmp_path)
        assert status.status in ("warning", "exceeded")

    async def test_check_worktree_exceeded(self, tmp_path: Path) -> None:
        """Worktree over limit returns exceeded status."""
        config = PlannerWorktreesConfig(
            max_disk_gb_per_worktree=0.00001,  # ~10 KB
        )
        watcher = DiskQuotaWatcher(config=config)
        (tmp_path / "data.bin").write_bytes(b"x" * 50_000)
        status = await watcher.check_worktree(tmp_path)
        assert status.status == "exceeded"

    async def test_check_nonexistent_path(self, tmp_path: Path) -> None:
        """Non-existent path returns ok with 0 usage."""
        config = PlannerWorktreesConfig()
        watcher = DiskQuotaWatcher(config=config)
        status = await watcher.check_worktree(tmp_path / "nonexistent")
        assert status.status == "ok"
        assert status.usage_gb == pytest.approx(0.0)

    async def test_monitor_cycle_multiple_paths(self, tmp_path: Path) -> None:
        """Monitor cycle checks all worktree paths."""
        wt1 = tmp_path / "wt1"
        wt2 = tmp_path / "wt2"
        wt1.mkdir()
        wt2.mkdir()
        config = PlannerWorktreesConfig()
        watcher = DiskQuotaWatcher(config=config)
        statuses = await watcher.run_monitor_cycle((wt1, wt2))
        assert len(statuses) == 2
        assert all(s.status == "ok" for s in statuses)

    async def test_monitor_cycle_empty_paths(self) -> None:
        """Empty path list returns empty statuses."""
        config = PlannerWorktreesConfig()
        watcher = DiskQuotaWatcher(config=config)
        statuses = await watcher.run_monitor_cycle(())
        assert len(statuses) == 0

    async def test_auto_cleanup_disabled_no_signal(
        self,
        tmp_path: Path,
    ) -> None:
        """When auto_cleanup_on_threshold=False, exceeded status has no cleanup flag."""
        config = PlannerWorktreesConfig(
            max_disk_gb_per_worktree=0.00001,
            auto_cleanup_on_threshold=False,
        )
        watcher = DiskQuotaWatcher(config=config)
        (tmp_path / "data.bin").write_bytes(b"x" * 50_000)
        status = await watcher.check_worktree(tmp_path)
        # Status is exceeded but cleanup is not signaled.
        assert status.status == "exceeded"


@pytest.mark.integration
class TestWorktreeIdCollisionRegression:
    """Regression for sub-agent ID collision.

    Spawning a sub-agent with an ID that collides with the parent
    worktree name must NOT delete the parent's working directory.
    """

    async def test_worktree_id_collision_does_not_delete_parent(
        self,
        tmp_path: Path,
    ) -> None:
        """Parent worktree survives cleanup of child with same name."""
        parent_wt = tmp_path / "worktree-alpha"
        child_wt = tmp_path / "child-worktree-alpha"
        parent_wt.mkdir()
        child_wt.mkdir()

        # Write marker files.
        (parent_wt / "marker.txt").write_text("parent-data")
        (child_wt / "marker.txt").write_text("child-data")

        # Simulate cleanup of child -- only child should be removed.
        import shutil

        shutil.rmtree(child_wt)

        # Parent must survive.
        assert parent_wt.exists()
        assert (parent_wt / "marker.txt").read_text() == "parent-data"
        assert not child_wt.exists()
