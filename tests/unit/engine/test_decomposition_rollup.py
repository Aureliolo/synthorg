"""Tests for status rollup computation."""

import pytest

from ai_company.core.enums import TaskStatus
from ai_company.engine.decomposition.rollup import StatusRollup


class TestStatusRollup:
    """Tests for StatusRollup.compute()."""

    @pytest.mark.unit
    def test_all_completed(self) -> None:
        """All COMPLETED -> derived COMPLETED."""
        rollup = StatusRollup.compute(
            "task-1",
            (TaskStatus.COMPLETED, TaskStatus.COMPLETED, TaskStatus.COMPLETED),
        )
        assert rollup.derived_parent_status == TaskStatus.COMPLETED
        assert rollup.total == 3
        assert rollup.completed == 3

    @pytest.mark.unit
    def test_all_cancelled(self) -> None:
        """All CANCELLED -> derived CANCELLED."""
        rollup = StatusRollup.compute(
            "task-1",
            (TaskStatus.CANCELLED, TaskStatus.CANCELLED),
        )
        assert rollup.derived_parent_status == TaskStatus.CANCELLED

    @pytest.mark.unit
    def test_any_failed(self) -> None:
        """Any FAILED -> derived FAILED."""
        rollup = StatusRollup.compute(
            "task-1",
            (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CREATED),
        )
        assert rollup.derived_parent_status == TaskStatus.FAILED

    @pytest.mark.unit
    def test_any_in_progress(self) -> None:
        """Any IN_PROGRESS -> derived IN_PROGRESS."""
        rollup = StatusRollup.compute(
            "task-1",
            (TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED, TaskStatus.CREATED),
        )
        assert rollup.derived_parent_status == TaskStatus.IN_PROGRESS

    @pytest.mark.unit
    def test_blocked_no_in_progress(self) -> None:
        """BLOCKED with no IN_PROGRESS -> derived BLOCKED."""
        rollup = StatusRollup.compute(
            "task-1",
            (TaskStatus.COMPLETED, TaskStatus.BLOCKED),
        )
        assert rollup.derived_parent_status == TaskStatus.BLOCKED

    @pytest.mark.unit
    def test_empty_statuses(self) -> None:
        """Empty statuses -> derived CREATED with all counts zero."""
        rollup = StatusRollup.compute("task-1", ())
        assert rollup.derived_parent_status == TaskStatus.CREATED
        assert rollup.total == 0
        assert rollup.completed == 0
        assert rollup.failed == 0
        assert rollup.in_progress == 0
        assert rollup.blocked == 0
        assert rollup.cancelled == 0

    @pytest.mark.unit
    def test_pending_work(self) -> None:
        """Some CREATED subtasks with completed ones -> IN_PROGRESS."""
        rollup = StatusRollup.compute(
            "task-1",
            (TaskStatus.COMPLETED, TaskStatus.CREATED, TaskStatus.ASSIGNED),
        )
        assert rollup.derived_parent_status == TaskStatus.IN_PROGRESS

    @pytest.mark.unit
    def test_rollup_counts(self) -> None:
        """Verify all count fields are populated correctly."""
        rollup = StatusRollup.compute(
            "task-1",
            (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.IN_PROGRESS,
                TaskStatus.BLOCKED,
                TaskStatus.CANCELLED,
            ),
        )
        assert rollup.total == 5
        assert rollup.completed == 1
        assert rollup.failed == 1
        assert rollup.in_progress == 1
        assert rollup.blocked == 1
        assert rollup.cancelled == 1
