"""Tests for typed args of MCP tasks + activities domain."""

import pytest
from pydantic import ValidationError

from synthorg.core.task_enums import TaskStatus
from synthorg.meta.mcp.domains._tasks_args import (
    ActivitiesListArgs,
    TasksCancelArgs,
    TasksCreateArgs,
    TasksDeleteArgs,
    TasksGetArgs,
    TasksListArgs,
    TasksTransitionArgs,
    TasksUpdateArgs,
)


class TestTasksListArgs:
    @pytest.mark.unit
    def test_defaults(self) -> None:
        args = TasksListArgs()
        assert args.offset == 0
        assert args.limit == 50

    @pytest.mark.unit
    def test_pagination_bounds(self) -> None:
        with pytest.raises(ValidationError):
            TasksListArgs(offset=-1)
        with pytest.raises(ValidationError):
            TasksListArgs(limit=0)
        with pytest.raises(ValidationError):
            TasksListArgs(limit=501)


class TestTasksCRUD:
    @pytest.mark.unit
    def test_get(self) -> None:
        args = TasksGetArgs(task_id="t1")
        assert args.task_id == "t1"

    @pytest.mark.unit
    def test_create_carries_task_data(self) -> None:
        args = TasksCreateArgs(task_data={"title": "ship it"})
        assert args.task_data == {"title": "ship it"}
        with pytest.raises(ValidationError):
            TasksCreateArgs.model_validate({})

    @pytest.mark.unit
    def test_update_requires_updates(self) -> None:
        with pytest.raises(ValidationError):
            TasksUpdateArgs.model_validate({"task_id": "t1"})

    @pytest.mark.unit
    def test_transition(self) -> None:
        args = TasksTransitionArgs.model_validate(
            {"task_id": "t1", "target_status": "completed"},
        )
        assert args.target_status is TaskStatus.COMPLETED
        with pytest.raises(ValidationError):
            TasksTransitionArgs.model_validate(
                {"task_id": "t1", "target_status": "bogus"},
            )


class TestDestructiveOps:
    @pytest.mark.unit
    def test_delete_requires_confirm_true(self) -> None:
        TasksDeleteArgs(task_id="t1", confirm=True, reason="cleanup")
        with pytest.raises(ValidationError):
            TasksDeleteArgs.model_validate(
                {"task_id": "t1", "confirm": False, "reason": "x"},
            )

    @pytest.mark.unit
    def test_cancel_requires_non_blank_reason(self) -> None:
        with pytest.raises(ValidationError):
            TasksCancelArgs(task_id="t1", confirm=True, reason="   ")

    @pytest.mark.unit
    def test_truthy_non_bool_confirm_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TasksDeleteArgs.model_validate(
                {"task_id": "t1", "confirm": 1, "reason": "x"},
            )


class TestActivitiesListArgs:
    @pytest.mark.unit
    def test_defaults(self) -> None:
        args = ActivitiesListArgs()
        assert args.window_hours is None

    @pytest.mark.unit
    @pytest.mark.parametrize("hours", [24, 48, 168])
    def test_lookback_valid(self, hours: int) -> None:
        """Any positive lookback window is accepted."""
        args = ActivitiesListArgs.model_validate({"window_hours": hours})
        assert args.window_hours == hours

    @pytest.mark.unit
    def test_lookback_invalid_rejected(self) -> None:
        """Non-positive lookback windows are rejected."""
        with pytest.raises(ValidationError):
            ActivitiesListArgs.model_validate({"window_hours": 0})
