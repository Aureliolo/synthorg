"""Conformance tests for workflow execution repository implementations.

Tests run against both SQLite and Postgres backends via the ``backend``
parametrized fixture.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import (
    WorkflowEdgeType,
    WorkflowExecutionStatus,
    WorkflowNodeExecutionStatus,
    WorkflowNodeType,
    WorkflowType,
)
from synthorg.core.persistence_errors import (
    DuplicateRecordError,
    PersistenceVersionConflictError,
    RecordNotFoundError,
)
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from synthorg.engine.workflow.execution_models import (
    WorkflowExecution,
    WorkflowNodeExecution,
)
from synthorg.persistence.protocol import PersistenceBackend


def _make_workflow_definition(
    definition_id: str = "wf-test",
    name: str = "Test Workflow",
) -> WorkflowDefinition:
    """Build a valid WorkflowDefinition with START, TASK, and END nodes."""
    return WorkflowDefinition(
        id=definition_id,
        name=name,
        description="Test workflow",
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        nodes=(
            WorkflowNode(
                id=f"{definition_id}-start",
                type=WorkflowNodeType.START,
                label="Start",
                position_x=0.0,
                position_y=0.0,
            ),
            WorkflowNode(
                id=f"{definition_id}-task",
                type=WorkflowNodeType.TASK,
                label="Do work",
                position_x=100.0,
                position_y=0.0,
            ),
            WorkflowNode(
                id=f"{definition_id}-end",
                type=WorkflowNodeType.END,
                label="End",
                position_x=200.0,
                position_y=0.0,
            ),
        ),
        edges=(
            WorkflowEdge(
                id=f"{definition_id}-e1",
                source_node_id=f"{definition_id}-start",
                target_node_id=f"{definition_id}-task",
                type=WorkflowEdgeType.SEQUENTIAL,
            ),
            WorkflowEdge(
                id=f"{definition_id}-e2",
                source_node_id=f"{definition_id}-task",
                target_node_id=f"{definition_id}-end",
                type=WorkflowEdgeType.SEQUENTIAL,
            ),
        ),
        created_by="admin",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        revision=1,
    )


def _make_workflow_execution(
    execution_id: str = "exec-test",
    definition_id: str = "wf-test",
    **overrides: object,
) -> WorkflowExecution:
    """Build a valid WorkflowExecution."""
    now = datetime.now(UTC)
    defaults: dict[str, object] = {
        "id": execution_id,
        "definition_id": definition_id,
        "definition_revision": 1,
        "status": WorkflowExecutionStatus.RUNNING,
        "node_executions": (
            WorkflowNodeExecution(
                node_id=f"{definition_id}-start",
                node_type=WorkflowNodeType.START,
                status=WorkflowNodeExecutionStatus.COMPLETED,
            ),
        ),
        "activated_by": "admin",
        "project": "test-project",
        "created_at": now,
        "updated_at": now,
        "version": 1,
    }
    defaults.update(overrides)
    return WorkflowExecution.model_validate(defaults)


@pytest.mark.integration
class TestWorkflowExecutionRepository:
    """Conformance tests for WorkflowExecutionRepository."""

    async def test_save_and_get_workflow_execution(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Save and retrieve a workflow execution."""
        defn_repo = backend.workflow_definitions
        exec_repo = backend.workflow_executions

        # Save parent definition first
        defn = _make_workflow_definition("wf-001", "Test")
        await defn_repo.save(defn)

        execution = _make_workflow_execution(
            execution_id="exec-001",
            definition_id="wf-001",
        )

        await exec_repo.save(execution)
        retrieved = await exec_repo.get("exec-001")

        assert retrieved is not None
        assert retrieved.id == "exec-001"
        assert retrieved.definition_id == "wf-001"
        assert retrieved.status == WorkflowExecutionStatus.RUNNING
        assert len(retrieved.node_executions) == 1

    async def test_get_nonexistent_execution(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Get non-existent execution returns None."""
        repo = backend.workflow_executions
        result = await repo.get("nonexistent")
        assert result is None

    async def test_list_by_definition(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """List executions by definition ID."""
        defn_repo = backend.workflow_definitions
        exec_repo = backend.workflow_executions
        now = datetime.now(UTC)

        # Save parent definition first
        defn = _make_workflow_definition("wf-001", "Test")
        await defn_repo.save(defn)

        exec1 = _make_workflow_execution(
            execution_id="exec-def-001",
            definition_id="wf-001",
            status=WorkflowExecutionStatus.COMPLETED,
            completed_at=now,
        )
        exec2 = _make_workflow_execution(
            execution_id="exec-def-002",
            definition_id="wf-001",
            status=WorkflowExecutionStatus.RUNNING,
        )

        await exec_repo.save(exec1)
        await exec_repo.save(exec2)

        executions = await exec_repo.list_by_definition("wf-001")
        assert len(executions) >= 2
        ids = {e.id for e in executions}
        assert "exec-def-001" in ids
        assert "exec-def-002" in ids

    async def test_list_by_status(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """List executions filtered by status."""
        defn_repo = backend.workflow_definitions
        exec_repo = backend.workflow_executions
        now = datetime.now(UTC)

        # Save parent definition first
        defn = _make_workflow_definition("wf-002", "Test")
        await defn_repo.save(defn)

        running = _make_workflow_execution(
            execution_id="exec-running",
            definition_id="wf-002",
            status=WorkflowExecutionStatus.RUNNING,
        )
        completed = _make_workflow_execution(
            execution_id="exec-completed",
            definition_id="wf-002",
            status=WorkflowExecutionStatus.COMPLETED,
            completed_at=now,
        )

        await exec_repo.save(running)
        await exec_repo.save(completed)

        running_only = await exec_repo.list_by_status(WorkflowExecutionStatus.RUNNING)
        assert len(running_only) >= 1
        assert any(e.id == "exec-running" for e in running_only)

    async def test_list_by_definition_respects_limit(
        self,
        backend: PersistenceBackend,
    ) -> None:
        defn_repo = backend.workflow_definitions
        exec_repo = backend.workflow_executions
        defn = _make_workflow_definition("wf-lim", "Test")
        await defn_repo.save(defn)
        for i in range(5):
            await exec_repo.save(
                _make_workflow_execution(
                    execution_id=f"exec-lim-{i}",
                    definition_id="wf-lim",
                    status=WorkflowExecutionStatus.PENDING,
                ),
            )

        rows = await exec_repo.list_by_definition("wf-lim", limit=3)
        assert len(rows) <= 3

    async def test_list_by_status_respects_limit(
        self,
        backend: PersistenceBackend,
    ) -> None:
        defn_repo = backend.workflow_definitions
        exec_repo = backend.workflow_executions
        defn = _make_workflow_definition("wf-lim-status", "Test")
        await defn_repo.save(defn)
        for i in range(5):
            await exec_repo.save(
                _make_workflow_execution(
                    execution_id=f"exec-lim-st-{i}",
                    definition_id="wf-lim-status",
                    status=WorkflowExecutionStatus.PENDING,
                ),
            )

        rows = await exec_repo.list_by_status(WorkflowExecutionStatus.PENDING, limit=3)
        assert len(rows) <= 3

    async def test_find_by_task_id(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Find running execution by task ID."""
        defn_repo = backend.workflow_definitions
        exec_repo = backend.workflow_executions

        # Save parent definition first
        defn = _make_workflow_definition("wf-003", "Test")
        await defn_repo.save(defn)

        execution = _make_workflow_execution(
            execution_id="exec-task",
            definition_id="wf-003",
            node_executions=(
                WorkflowNodeExecution(
                    node_id="wf-003-task",
                    node_type=WorkflowNodeType.TASK,
                    status=WorkflowNodeExecutionStatus.TASK_COMPLETED,
                    task_id="task-123",
                ),
            ),
        )

        await exec_repo.save(execution)
        found = await exec_repo.find_by_task_id("task-123")

        assert found is not None
        assert found.id == "exec-task"

    async def test_find_by_task_id_not_found(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Find by task ID returns None when not found."""
        repo = backend.workflow_executions
        result = await repo.find_by_task_id("nonexistent-task")
        assert result is None

    async def test_duplicate_insert_raises_error(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Inserting duplicate ID raises DuplicateRecordError."""
        defn_repo = backend.workflow_definitions
        exec_repo = backend.workflow_executions

        # Save parent definition first
        defn = _make_workflow_definition("wf-001", "Test")
        await defn_repo.save(defn)

        execution = _make_workflow_execution(
            execution_id="exec-dup",
            definition_id="wf-001",
        )

        await exec_repo.save(execution)

        with pytest.raises(DuplicateRecordError):
            await exec_repo.save(execution)

    async def test_update_with_version_conflict(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Update with wrong version raises PersistenceVersionConflictError."""
        defn_repo = backend.workflow_definitions
        exec_repo = backend.workflow_executions

        # Save parent definition first
        defn = _make_workflow_definition("wf-001", "Test")
        await defn_repo.save(defn)

        execution = _make_workflow_execution(
            execution_id="exec-version",
            definition_id="wf-001",
        )
        await exec_repo.save(execution)

        now = datetime.now(UTC)
        stale = _make_workflow_execution(
            execution_id="exec-version",
            definition_id="wf-001",
            status=WorkflowExecutionStatus.COMPLETED,
            completed_at=now,
            version=5,
        )

        with pytest.raises(PersistenceVersionConflictError):
            await exec_repo.save(stale)

    async def test_update_after_delete_raises_record_not_found(
        self,
        backend: PersistenceBackend,
    ) -> None:
        # Differentiates a delete race from a true version mismatch
        # so the API surface keeps the 404 (not-found) path distinct
        # from the 409 (conflict) path.
        defn_repo = backend.workflow_definitions
        exec_repo = backend.workflow_executions

        defn = _make_workflow_definition("wf-delete-race", "Test")
        await defn_repo.save(defn)

        execution = _make_workflow_execution(
            execution_id="exec-delete-race",
            definition_id="wf-delete-race",
        )
        await exec_repo.save(execution)
        deleted = await exec_repo.delete("exec-delete-race")
        assert deleted is True

        updated = _make_workflow_execution(
            execution_id="exec-delete-race",
            definition_id="wf-delete-race",
            status=WorkflowExecutionStatus.COMPLETED,
            completed_at=datetime.now(UTC),
            version=2,
        )

        with pytest.raises(RecordNotFoundError):
            await exec_repo.save(updated)

    async def test_delete_execution(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Delete a workflow execution."""
        defn_repo = backend.workflow_definitions
        exec_repo = backend.workflow_executions

        # Save parent definition first (needed for FK constraint)
        defn = _make_workflow_definition("wf-for-exec-delete", "Test")
        await defn_repo.save(defn)

        execution = _make_workflow_execution(
            execution_id="exec-for-delete",
            definition_id="wf-for-exec-delete",
        )
        await exec_repo.save(execution)

        deleted = await exec_repo.delete("exec-for-delete")
        assert deleted is True

        retrieved = await exec_repo.get("exec-for-delete")
        assert retrieved is None
