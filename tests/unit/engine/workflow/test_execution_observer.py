"""Tests for WorkflowExecutionObserver."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.core.enums import TaskStatus
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskStateChanged
from synthorg.engine.workflow.execution_observer import (
    WorkflowExecutionObserver,
)


# Concrete fakes (not spec=Protocol) per the test guideline:
# ``MagicMock(spec=Protocol)`` does not bound attribute access, so a
# typo would still mock-out instead of raising. The observer's
# constructor only stashes the repos and never calls any method on
# them, so the fakes can be empty marker classes.
class _FakeDefinitionRepo:
    """Concrete double for ``WorkflowDefinitionRepository``."""


class _FakeExecutionRepo:
    """Concrete double for ``WorkflowExecutionRepository``."""


def _make_event(
    task_id: str = "task-001",
    new_status: TaskStatus = TaskStatus.COMPLETED,
) -> TaskStateChanged:
    """Build a minimal TaskStateChanged event."""
    return TaskStateChanged(
        mutation_type="transition",
        request_id="req-test",
        requested_by="test",
        task_id=task_id,
        task=None,
        previous_status=TaskStatus.IN_PROGRESS,
        new_status=new_status,
        version=2,
        reason="test transition",
        timestamp=datetime.now(UTC),
    )


class TestWorkflowExecutionObserver:
    """Tests for the WorkflowExecutionObserver bridge."""

    @pytest.mark.unit
    def test_constructor_wires_service(self) -> None:
        """Observer creates a WorkflowExecutionService with the given deps."""
        definition_repo = MagicMock(spec=_FakeDefinitionRepo)
        execution_repo = MagicMock(spec=_FakeExecutionRepo)
        task_engine = MagicMock(spec=TaskEngine)

        observer = WorkflowExecutionObserver(
            definition_repo=definition_repo,
            execution_repo=execution_repo,
            task_engine=task_engine,
            max_subworkflow_depth=16,
        )

        service = observer._service
        assert service._definition_repo is definition_repo
        assert service._execution_repo is execution_repo
        assert service._task_engine is task_engine
        assert service._max_subworkflow_depth == 16

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "status",
        [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED],
    )
    async def test_call_delegates_to_service(
        self,
        status: TaskStatus,
    ) -> None:
        """__call__ forwards all task events to service."""
        observer = WorkflowExecutionObserver(
            definition_repo=MagicMock(spec=_FakeDefinitionRepo),
            execution_repo=MagicMock(spec=_FakeExecutionRepo),
            task_engine=MagicMock(spec=TaskEngine),
            max_subworkflow_depth=16,
        )

        event = _make_event(new_status=status)
        mock_handle = AsyncMock()
        with patch.object(
            observer._service,
            "handle_task_state_changed",
            mock_handle,
        ):
            await observer(event)

        mock_handle.assert_awaited_once_with(event)
