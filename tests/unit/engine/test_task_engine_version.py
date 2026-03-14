"""Unit tests for VersionTracker."""

import pytest

from synthorg.engine.errors import TaskVersionConflictError
from synthorg.engine.task_engine_version import VersionTracker


@pytest.mark.unit
class TestVersionTracker:
    """Tests for in-memory per-task version counter."""

    def test_seed_sets_version_to_one(self) -> None:
        vt = VersionTracker()
        vt.seed("task-1")
        assert vt.get("task-1") == 1

    def test_seed_is_idempotent(self) -> None:
        vt = VersionTracker()
        vt.seed("task-1")
        vt.seed("task-1")
        assert vt.get("task-1") == 1

    def test_seed_does_not_reset_after_bump(self) -> None:
        vt = VersionTracker()
        vt.set_initial("task-1", 1)
        vt.bump("task-1")
        vt.seed("task-1")
        assert vt.get("task-1") == 2

    def test_set_initial(self) -> None:
        vt = VersionTracker()
        vt.set_initial("task-1", 5)
        assert vt.get("task-1") == 5

    def test_set_initial_overwrites(self) -> None:
        vt = VersionTracker()
        vt.set_initial("task-1", 5)
        vt.set_initial("task-1", 10)
        assert vt.get("task-1") == 10

    def test_bump_increments(self) -> None:
        vt = VersionTracker()
        vt.set_initial("task-1", 1)
        assert vt.bump("task-1") == 2
        assert vt.bump("task-1") == 3

    def test_bump_auto_seeds(self) -> None:
        """Bumping an unknown task seeds at 1, then increments to 2."""
        vt = VersionTracker()
        assert vt.bump("task-1") == 2

    def test_get_returns_zero_for_untracked(self) -> None:
        vt = VersionTracker()
        assert vt.get("task-unknown") == 0

    def test_remove_clears_tracking(self) -> None:
        vt = VersionTracker()
        vt.set_initial("task-1", 3)
        vt.remove("task-1")
        assert vt.get("task-1") == 0

    def test_remove_nonexistent_is_noop(self) -> None:
        vt = VersionTracker()
        vt.remove("task-unknown")  # no error

    def test_check_passes_when_none(self) -> None:
        vt = VersionTracker()
        vt.check("task-1", None)  # no error

    def test_check_passes_when_version_matches(self) -> None:
        vt = VersionTracker()
        vt.set_initial("task-1", 3)
        vt.check("task-1", 3)  # no error

    def test_check_raises_on_conflict(self) -> None:
        vt = VersionTracker()
        vt.set_initial("task-1", 3)
        with pytest.raises(
            TaskVersionConflictError,
            match="expected 99, current 3",
        ):
            vt.check("task-1", 99)

    def test_check_seeds_unknown_task(self) -> None:
        """First check on unknown task seeds at 1 then validates."""
        vt = VersionTracker()
        vt.check("task-1", 1)  # seeds at 1, matches
        assert vt.get("task-1") == 1

    def test_check_seeds_then_rejects_mismatch(self) -> None:
        vt = VersionTracker()
        with pytest.raises(TaskVersionConflictError, match="expected 5"):
            vt.check("task-1", 5)

    def test_set_initial_rejects_zero(self) -> None:
        """set_initial must reject version=0."""
        vt = VersionTracker()
        with pytest.raises(ValueError, match="must be >= 1"):
            vt.set_initial("task-1", 0)

    def test_set_initial_rejects_negative(self) -> None:
        """set_initial must reject negative versions."""
        vt = VersionTracker()
        with pytest.raises(ValueError, match="must be >= 1"):
            vt.set_initial("task-1", -5)
