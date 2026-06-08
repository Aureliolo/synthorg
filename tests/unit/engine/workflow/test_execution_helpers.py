"""Tests for _update_node_status and _all_tasks_completed helpers."""

import pytest

from synthorg.engine.workflow.enums import (
    WorkflowExecutionStatus,
    WorkflowNodeExecutionStatus,
    WorkflowNodeType,
)
from synthorg.engine.workflow.execution_lifecycle import (
    _all_tasks_completed,
    _update_node_status,
)
from synthorg.engine.workflow.execution_models import (
    WorkflowExecution,
    WorkflowNodeExecution,
)

# -- _update_node_status helper tests ----------------------------------------


class TestUpdateNodeStatus:
    """Tests for the _update_node_status helper."""

    @pytest.mark.unit
    def test_updates_matching_node(self) -> None:
        exe = WorkflowExecution(
            id="wfexec-test",
            definition_id="wf-1",
            definition_revision=1,
            status=WorkflowExecutionStatus.RUNNING,
            node_executions=(
                WorkflowNodeExecution(
                    node_id="task-1",
                    node_type=WorkflowNodeType.TASK,
                    status=WorkflowNodeExecutionStatus.TASK_CREATED,
                    task_id="t-1",
                ),
            ),
            activated_by="test",
            project="proj-1",
        )
        result = _update_node_status(
            exe,
            "t-1",
            WorkflowNodeExecutionStatus.TASK_COMPLETED,
        )
        assert result.node_executions[0].status is (
            WorkflowNodeExecutionStatus.TASK_COMPLETED
        )
        assert result.node_executions[0].task_id == "t-1"
        assert result.version == exe.version + 1

    @pytest.mark.unit
    def test_preserves_other_nodes(self) -> None:
        exe = WorkflowExecution(
            id="wfexec-test",
            definition_id="wf-1",
            definition_revision=1,
            status=WorkflowExecutionStatus.RUNNING,
            node_executions=(
                WorkflowNodeExecution(
                    node_id="task-1",
                    node_type=WorkflowNodeType.TASK,
                    status=WorkflowNodeExecutionStatus.TASK_CREATED,
                    task_id="t-1",
                ),
                WorkflowNodeExecution(
                    node_id="task-2",
                    node_type=WorkflowNodeType.TASK,
                    status=WorkflowNodeExecutionStatus.TASK_CREATED,
                    task_id="t-2",
                ),
            ),
            activated_by="test",
            project="proj-1",
        )
        result = _update_node_status(
            exe,
            "t-1",
            WorkflowNodeExecutionStatus.TASK_COMPLETED,
        )
        nmap = {ne.node_id: ne for ne in result.node_executions}
        assert nmap["task-1"].status is WorkflowNodeExecutionStatus.TASK_COMPLETED
        assert nmap["task-2"].status is WorkflowNodeExecutionStatus.TASK_CREATED

    @pytest.mark.unit
    def test_raises_for_missing_task_id(self) -> None:
        exe = WorkflowExecution(
            id="wfexec-test",
            definition_id="wf-1",
            definition_revision=1,
            status=WorkflowExecutionStatus.RUNNING,
            node_executions=(
                WorkflowNodeExecution(
                    node_id="task-1",
                    node_type=WorkflowNodeType.TASK,
                    status=WorkflowNodeExecutionStatus.TASK_CREATED,
                    task_id="t-1",
                ),
            ),
            activated_by="test",
            project="proj-1",
        )
        with pytest.raises(ValueError, match="not found in execution"):
            _update_node_status(
                exe,
                "nonexistent-task-id",
                WorkflowNodeExecutionStatus.TASK_COMPLETED,
            )


# -- _all_tasks_completed helper tests ---------------------------------------


class TestAllTasksCompleted:
    """Tests for the _all_tasks_completed helper."""

    @pytest.mark.unit
    def test_all_completed(self) -> None:
        exe = WorkflowExecution(
            id="wfexec-test",
            definition_id="wf-1",
            definition_revision=1,
            status=WorkflowExecutionStatus.RUNNING,
            node_executions=(
                WorkflowNodeExecution(
                    node_id="s",
                    node_type=WorkflowNodeType.START,
                    status=WorkflowNodeExecutionStatus.COMPLETED,
                ),
                WorkflowNodeExecution(
                    node_id="t1",
                    node_type=WorkflowNodeType.TASK,
                    status=WorkflowNodeExecutionStatus.TASK_COMPLETED,
                    task_id="task-1",
                ),
                WorkflowNodeExecution(
                    node_id="e",
                    node_type=WorkflowNodeType.END,
                    status=WorkflowNodeExecutionStatus.COMPLETED,
                ),
            ),
            activated_by="test",
            project="proj-1",
        )
        assert _all_tasks_completed(exe) is True

    @pytest.mark.unit
    def test_not_all_completed(self) -> None:
        exe = WorkflowExecution(
            id="wfexec-test",
            definition_id="wf-1",
            definition_revision=1,
            status=WorkflowExecutionStatus.RUNNING,
            node_executions=(
                WorkflowNodeExecution(
                    node_id="t1",
                    node_type=WorkflowNodeType.TASK,
                    status=WorkflowNodeExecutionStatus.TASK_COMPLETED,
                    task_id="task-1",
                ),
                WorkflowNodeExecution(
                    node_id="t2",
                    node_type=WorkflowNodeType.TASK,
                    status=WorkflowNodeExecutionStatus.TASK_CREATED,
                    task_id="task-2",
                ),
            ),
            activated_by="test",
            project="proj-1",
        )
        assert _all_tasks_completed(exe) is False

    @pytest.mark.unit
    def test_task_failed_returns_false(self) -> None:
        exe = WorkflowExecution(
            id="wfexec-test",
            definition_id="wf-1",
            definition_revision=1,
            status=WorkflowExecutionStatus.RUNNING,
            node_executions=(
                WorkflowNodeExecution(
                    node_id="t1",
                    node_type=WorkflowNodeType.TASK,
                    status=WorkflowNodeExecutionStatus.TASK_COMPLETED,
                    task_id="task-1",
                ),
                WorkflowNodeExecution(
                    node_id="t2",
                    node_type=WorkflowNodeType.TASK,
                    status=WorkflowNodeExecutionStatus.TASK_FAILED,
                    task_id="task-2",
                ),
            ),
            activated_by="test",
            project="proj-1",
        )
        assert _all_tasks_completed(exe) is False

    @pytest.mark.unit
    def test_skipped_tasks_ignored(self) -> None:
        exe = WorkflowExecution(
            id="wfexec-test",
            definition_id="wf-1",
            definition_revision=1,
            status=WorkflowExecutionStatus.RUNNING,
            node_executions=(
                WorkflowNodeExecution(
                    node_id="t1",
                    node_type=WorkflowNodeType.TASK,
                    status=WorkflowNodeExecutionStatus.TASK_COMPLETED,
                    task_id="task-1",
                ),
                WorkflowNodeExecution(
                    node_id="t2",
                    node_type=WorkflowNodeType.TASK,
                    status=WorkflowNodeExecutionStatus.SKIPPED,
                    skipped_reason="Branch not taken",
                ),
            ),
            activated_by="test",
            project="proj-1",
        )
        assert _all_tasks_completed(exe) is True

    @pytest.mark.unit
    def test_no_task_nodes_returns_true(self) -> None:
        """Vacuous truth: execution with only control nodes is complete."""
        exe = WorkflowExecution(
            id="wfexec-test",
            definition_id="wf-1",
            definition_revision=1,
            status=WorkflowExecutionStatus.RUNNING,
            node_executions=(
                WorkflowNodeExecution(
                    node_id="s",
                    node_type=WorkflowNodeType.START,
                    status=WorkflowNodeExecutionStatus.COMPLETED,
                ),
                WorkflowNodeExecution(
                    node_id="e",
                    node_type=WorkflowNodeType.END,
                    status=WorkflowNodeExecutionStatus.COMPLETED,
                ),
            ),
            activated_by="test",
            project="proj-1",
        )
        assert _all_tasks_completed(exe) is True
