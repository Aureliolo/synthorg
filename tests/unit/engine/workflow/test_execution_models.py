"""Tests for workflow execution models."""

import math
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.engine.workflow.enums import (
    WorkflowExecutionStatus,
    WorkflowNodeExecutionStatus,
    WorkflowNodeType,
)
from synthorg.engine.workflow.execution_models import (
    WorkflowExecution,
    WorkflowNodeExecution,
)

# ── WorkflowNodeExecution ──────────────────────────────────────────


class TestWorkflowNodeExecution:
    """WorkflowNodeExecution validation and immutability."""

    @pytest.mark.unit
    def test_valid_pending_node(self) -> None:
        node = WorkflowNodeExecution(
            node_id="task-1",
            node_type=WorkflowNodeType.TASK,
        )
        assert node.node_id == "task-1"
        assert node.node_type is WorkflowNodeType.TASK
        assert node.status is WorkflowNodeExecutionStatus.PENDING
        assert node.task_id is None
        assert node.skipped_reason is None

    @pytest.mark.unit
    def test_task_created_with_task_id(self) -> None:
        node = WorkflowNodeExecution(
            node_id="task-1",
            node_type=WorkflowNodeType.TASK,
            status=WorkflowNodeExecutionStatus.TASK_CREATED,
            task_id="task-abc123",
        )
        assert node.status is WorkflowNodeExecutionStatus.TASK_CREATED
        assert node.task_id == "task-abc123"

    @pytest.mark.unit
    def test_skipped_with_reason(self) -> None:
        node = WorkflowNodeExecution(
            node_id="task-2",
            node_type=WorkflowNodeType.TASK,
            status=WorkflowNodeExecutionStatus.SKIPPED,
            skipped_reason="Conditional branch not taken",
        )
        assert node.status is WorkflowNodeExecutionStatus.SKIPPED
        assert node.skipped_reason == "Conditional branch not taken"

    @pytest.mark.unit
    def test_control_node_completed(self) -> None:
        node = WorkflowNodeExecution(
            node_id="start-1",
            node_type=WorkflowNodeType.START,
            status=WorkflowNodeExecutionStatus.COMPLETED,
        )
        assert node.status is WorkflowNodeExecutionStatus.COMPLETED

    @pytest.mark.unit
    def test_frozen(self) -> None:
        node = WorkflowNodeExecution(
            node_id="task-1",
            node_type=WorkflowNodeType.TASK,
        )
        with pytest.raises(ValidationError):
            node.status = WorkflowNodeExecutionStatus.COMPLETED  # type: ignore[misc]

    @pytest.mark.unit
    def test_blank_node_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="node_id"):
            WorkflowNodeExecution(
                node_id="  ",
                node_type=WorkflowNodeType.TASK,
            )

    @pytest.mark.unit
    def test_blank_task_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="task_id"):
            WorkflowNodeExecution(
                node_id="task-1",
                node_type=WorkflowNodeType.TASK,
                task_id="  ",
            )


# ── WorkflowExecution ─────────────────────────────────────────────


def _make_execution(**overrides: object) -> WorkflowExecution:
    """Build a WorkflowExecution with sensible defaults."""
    now = datetime.now(UTC)
    defaults: dict[str, object] = {
        "id": "wfexec-test001",
        "definition_id": "wfdef-abc123",
        "definition_revision": 1,
        "activated_by": "test-user",
        "project": "test-project",
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return WorkflowExecution.model_validate(defaults)


class TestWorkflowExecution:
    """WorkflowExecution validation, defaults, and immutability."""

    @pytest.mark.unit
    def test_valid_defaults(self) -> None:
        exe = _make_execution()
        assert exe.id == "wfexec-test001"
        assert exe.definition_id == "wfdef-abc123"
        assert exe.definition_revision == 1
        assert exe.status is WorkflowExecutionStatus.PENDING
        assert exe.node_executions == ()
        assert exe.activated_by == "test-user"
        assert exe.project == "test-project"
        assert exe.completed_at is None
        assert exe.error is None
        assert exe.version == 1

    @pytest.mark.unit
    def test_with_node_executions(self) -> None:
        nodes = (
            WorkflowNodeExecution(
                node_id="start-1",
                node_type=WorkflowNodeType.START,
                status=WorkflowNodeExecutionStatus.COMPLETED,
            ),
            WorkflowNodeExecution(
                node_id="task-1",
                node_type=WorkflowNodeType.TASK,
                status=WorkflowNodeExecutionStatus.TASK_CREATED,
                task_id="task-xyz",
            ),
        )
        exe = _make_execution(node_executions=nodes)
        assert len(exe.node_executions) == 2
        assert exe.node_executions[1].task_id == "task-xyz"

    @pytest.mark.unit
    def test_running_status(self) -> None:
        exe = _make_execution(status=WorkflowExecutionStatus.RUNNING)
        assert exe.status is WorkflowExecutionStatus.RUNNING

    @pytest.mark.unit
    def test_failed_with_error(self) -> None:
        now = datetime.now(UTC)
        exe = _make_execution(
            status=WorkflowExecutionStatus.FAILED,
            error="Task creation failed",
            completed_at=now,
        )
        assert exe.status is WorkflowExecutionStatus.FAILED
        assert exe.error == "Task creation failed"
        assert exe.completed_at == now

    @pytest.mark.unit
    def test_completed_with_timestamp(self) -> None:
        now = datetime.now(UTC)
        exe = _make_execution(
            status=WorkflowExecutionStatus.COMPLETED,
            completed_at=now,
        )
        assert exe.status is WorkflowExecutionStatus.COMPLETED
        assert exe.completed_at == now

    @pytest.mark.unit
    def test_frozen(self) -> None:
        exe = _make_execution()
        with pytest.raises(ValidationError):
            exe.status = WorkflowExecutionStatus.RUNNING  # type: ignore[misc]

    @pytest.mark.unit
    def test_blank_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="id"):
            _make_execution(id="  ")

    @pytest.mark.unit
    def test_blank_definition_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="definition_id"):
            _make_execution(definition_id="  ")

    @pytest.mark.unit
    def test_blank_activated_by_rejected(self) -> None:
        with pytest.raises(ValidationError, match="activated_by"):
            _make_execution(activated_by="  ")

    @pytest.mark.unit
    def test_blank_project_rejected(self) -> None:
        with pytest.raises(ValidationError, match="project"):
            _make_execution(project="  ")

    @pytest.mark.unit
    def test_version_must_be_ge_1(self) -> None:
        with pytest.raises(ValidationError, match="version"):
            _make_execution(version=0)

    @pytest.mark.unit
    def test_definition_revision_must_be_ge_1(self) -> None:
        with pytest.raises(ValidationError, match="definition_revision"):
            _make_execution(definition_revision=0)

    @pytest.mark.unit
    def test_nan_rejected_in_version(self) -> None:
        with pytest.raises(ValidationError):
            _make_execution(version=math.nan)

    @pytest.mark.unit
    def test_nan_rejected_in_definition_revision(self) -> None:
        with pytest.raises(ValidationError):
            _make_execution(definition_revision=math.nan)


# ── Negative cross-field validator tests ─────────────────────────


class TestNodeExecutionCrossFieldValidators:
    """Verify _validate_status_fields rejects invalid combinations."""

    @pytest.mark.unit
    def test_task_created_without_task_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="task_id is required"):
            WorkflowNodeExecution(
                node_id="task-1",
                node_type=WorkflowNodeType.TASK,
                status=WorkflowNodeExecutionStatus.TASK_CREATED,
            )

    @pytest.mark.unit
    def test_pending_with_task_id_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="task_id must be None",
        ):
            WorkflowNodeExecution(
                node_id="task-1",
                node_type=WorkflowNodeType.TASK,
                status=WorkflowNodeExecutionStatus.PENDING,
                task_id="task-abc",
            )

    @pytest.mark.unit
    def test_skipped_without_reason_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="skipped_reason is required",
        ):
            WorkflowNodeExecution(
                node_id="task-1",
                node_type=WorkflowNodeType.TASK,
                status=WorkflowNodeExecutionStatus.SKIPPED,
            )

    @pytest.mark.unit
    def test_pending_with_skipped_reason_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="skipped_reason must be None",
        ):
            WorkflowNodeExecution(
                node_id="task-1",
                node_type=WorkflowNodeType.TASK,
                status=WorkflowNodeExecutionStatus.PENDING,
                skipped_reason="spurious reason",
            )


class TestTaskLinkedStatusOnNonTaskNodes:
    """Verify task-linked statuses rejected for non-TASK node types."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("node_type", "status"),
        [
            (WorkflowNodeType.START, WorkflowNodeExecutionStatus.TASK_COMPLETED),
            (WorkflowNodeType.START, WorkflowNodeExecutionStatus.TASK_FAILED),
            (WorkflowNodeType.END, WorkflowNodeExecutionStatus.TASK_COMPLETED),
            (WorkflowNodeType.CONDITIONAL, WorkflowNodeExecutionStatus.TASK_CREATED),
        ],
    )
    def test_task_linked_status_on_non_task_node_rejected(
        self,
        node_type: WorkflowNodeType,
        status: WorkflowNodeExecutionStatus,
    ) -> None:
        with pytest.raises(
            ValidationError,
            match="only valid for TASK nodes",
        ):
            WorkflowNodeExecution(
                node_id="node-1",
                node_type=node_type,
                status=status,
                task_id="task-abc",
            )


class TestExecutionCrossFieldValidators:
    """Verify WorkflowExecution cross-field validators."""

    @pytest.mark.unit
    def test_failed_without_error_rejected(self) -> None:
        with pytest.raises(ValidationError, match="error is required"):
            _make_execution(
                status=WorkflowExecutionStatus.FAILED,
                completed_at=datetime.now(UTC),
            )

    @pytest.mark.unit
    def test_running_with_error_rejected(self) -> None:
        with pytest.raises(ValidationError, match="error must be None"):
            _make_execution(
                status=WorkflowExecutionStatus.RUNNING,
                error="spurious error",
            )

    @pytest.mark.unit
    def test_completed_without_completed_at_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="completed_at is required",
        ):
            _make_execution(status=WorkflowExecutionStatus.COMPLETED)

    @pytest.mark.unit
    def test_pending_with_completed_at_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="completed_at must be None",
        ):
            _make_execution(
                status=WorkflowExecutionStatus.PENDING,
                completed_at=datetime.now(UTC),
            )

    @pytest.mark.unit
    def test_duplicate_node_ids_rejected(self) -> None:
        nodes = (
            WorkflowNodeExecution(
                node_id="task-1",
                node_type=WorkflowNodeType.TASK,
                status=WorkflowNodeExecutionStatus.COMPLETED,
            ),
            WorkflowNodeExecution(
                node_id="task-1",
                node_type=WorkflowNodeType.TASK,
                status=WorkflowNodeExecutionStatus.COMPLETED,
            ),
        )
        with pytest.raises(ValidationError, match="Duplicate node_execution"):
            _make_execution(node_executions=nodes)
