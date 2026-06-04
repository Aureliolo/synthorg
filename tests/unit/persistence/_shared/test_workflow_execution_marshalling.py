"""Tests for the shared workflow-execution marshalling helpers."""

import json
from datetime import UTC, datetime

import pytest

from synthorg.core.enums import (
    WorkflowExecutionStatus,
    WorkflowNodeExecutionStatus,
    WorkflowNodeType,
)
from synthorg.core.persistence_errors import QueryError
from synthorg.engine.workflow.execution_models import (
    WorkflowExecution,
    WorkflowNodeExecution,
)
from synthorg.persistence._shared.workflow_execution_marshalling import (
    build_workflow_execution_where,
    deserialize_node_executions,
    node_execution_payloads,
    row_to_workflow_execution,
)
from synthorg.persistence.workflow_execution_protocol import (
    WorkflowExecutionFilterSpec,
)

_NOW = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)


def _execution() -> WorkflowExecution:
    return WorkflowExecution(
        id="wfexec-abc123def456",
        definition_id="wfdef-1",
        definition_revision=1,
        status=WorkflowExecutionStatus.RUNNING,
        node_executions=(
            WorkflowNodeExecution(
                node_id="node-1",
                node_type=WorkflowNodeType.TASK,
                status=WorkflowNodeExecutionStatus.TASK_CREATED,
                task_id="task-1",
            ),
        ),
        activated_by="user-1",
        project="proj-1",
        created_at=_NOW,
        updated_at=_NOW,
        version=1,
    )


def _sqlite_data(execution: WorkflowExecution) -> dict[str, object]:
    """A SQLite-shaped row: node_executions as TEXT JSON, ISO timestamps."""
    return {
        "id": execution.id,
        "definition_id": execution.definition_id,
        "definition_revision": execution.definition_revision,
        "status": execution.status.value,
        "node_executions": json.dumps(node_execution_payloads(execution)),
        "activated_by": execution.activated_by,
        "project": execution.project,
        "created_at": execution.created_at.isoformat(),
        "updated_at": execution.updated_at.isoformat(),
        "completed_at": None,
        "error": None,
        "version": execution.version,
    }


@pytest.mark.unit
class TestDeserializeNodeExecutions:
    """``deserialize_node_executions`` absorbs both backend shapes."""

    def test_json_string_sqlite(self) -> None:
        execution = _execution()
        raw = json.dumps(node_execution_payloads(execution))

        assert deserialize_node_executions(raw) == execution.node_executions

    def test_native_list_postgres(self) -> None:
        execution = _execution()
        raw = node_execution_payloads(execution)

        assert deserialize_node_executions(raw) == execution.node_executions

    def test_none_is_empty(self) -> None:
        assert deserialize_node_executions(None) == ()


@pytest.mark.unit
class TestRowToWorkflowExecution:
    """``row_to_workflow_execution`` reconstructs from either backend shape."""

    def test_sqlite_round_trip(self) -> None:
        execution = _execution()
        result = row_to_workflow_execution(_sqlite_data(execution), execution.id)

        assert result == execution

    def test_postgres_native_shape(self) -> None:
        execution = _execution()
        data = _sqlite_data(execution)
        data["node_executions"] = node_execution_payloads(execution)
        data["created_at"] = _NOW
        data["updated_at"] = _NOW

        result = row_to_workflow_execution(data, execution.id)

        assert result == execution

    def test_corrupt_status_raises(self) -> None:
        data = _sqlite_data(_execution())
        data["status"] = "not-a-status"

        with pytest.raises(QueryError):
            row_to_workflow_execution(data, "ctx")


@pytest.mark.unit
class TestBuildWorkflowExecutionWhere:
    """``build_workflow_execution_where`` emits backend placeholders."""

    def test_empty_filter(self) -> None:
        where, params = build_workflow_execution_where(
            WorkflowExecutionFilterSpec(), placeholder="?"
        )

        assert where == "1=1"
        assert params == []

    def test_combined_sqlite(self) -> None:
        spec = WorkflowExecutionFilterSpec(
            definition_id="wfdef-1", status=WorkflowExecutionStatus.RUNNING
        )
        where, params = build_workflow_execution_where(spec, placeholder="?")

        assert where == "definition_id = ? AND status = ?"
        assert params == ["wfdef-1", WorkflowExecutionStatus.RUNNING.value]

    def test_status_postgres(self) -> None:
        spec = WorkflowExecutionFilterSpec(status=WorkflowExecutionStatus.COMPLETED)
        where, params = build_workflow_execution_where(spec, placeholder="%s")

        assert where == "status = %s"
        assert params == [WorkflowExecutionStatus.COMPLETED.value]
