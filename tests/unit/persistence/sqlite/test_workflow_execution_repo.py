"""Tests for SQLiteWorkflowExecutionRepository."""

from datetime import UTC, datetime

import aiosqlite
import pytest

from synthorg.core.enums import (
    WorkflowExecutionStatus,
    WorkflowNodeExecutionStatus,
    WorkflowNodeType,
)
from synthorg.core.persistence_errors import VersionConflictError
from synthorg.engine.workflow.execution_models import (
    WorkflowExecution,
    WorkflowNodeExecution,
)
from synthorg.persistence.sqlite.workflow_execution_repo import (
    SQLiteWorkflowExecutionRepository,
)


@pytest.fixture
def repo(
    migrated_db: aiosqlite.Connection,
) -> SQLiteWorkflowExecutionRepository:
    return SQLiteWorkflowExecutionRepository(migrated_db)


def _make_execution(
    execution_id: str = "wfexec-test001",
    **overrides: object,
) -> WorkflowExecution:
    """Build a WorkflowExecution with sensible defaults."""
    now = datetime.now(UTC)
    defaults: dict[str, object] = {
        "id": execution_id,
        "definition_id": "wfdef-abc123",
        "definition_revision": 1,
        "status": WorkflowExecutionStatus.RUNNING,
        "node_executions": (
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
        ),
        "activated_by": "test-user",
        "project": "test-project",
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return WorkflowExecution.model_validate(defaults)


class TestSaveAndGet:
    """Save and retrieve workflow executions."""

    @pytest.mark.unit
    async def test_save_and_get(
        self,
        repo: SQLiteWorkflowExecutionRepository,
    ) -> None:
        exe = _make_execution()
        await repo.save(exe)
        loaded = await repo.get("wfexec-test001")

        assert loaded is not None
        assert loaded.id == exe.id
        assert loaded.definition_id == exe.definition_id
        assert loaded.definition_revision == 1
        assert loaded.status is WorkflowExecutionStatus.RUNNING
        assert loaded.activated_by == "test-user"
        assert loaded.project == "test-project"
        assert loaded.version == 1

    @pytest.mark.unit
    async def test_node_executions_roundtrip(
        self,
        repo: SQLiteWorkflowExecutionRepository,
    ) -> None:
        exe = _make_execution()
        await repo.save(exe)
        loaded = await repo.get("wfexec-test001")

        assert loaded is not None
        assert len(loaded.node_executions) == 2
        ne0 = loaded.node_executions[0]
        assert ne0.node_id == "start-1"
        assert ne0.node_type is WorkflowNodeType.START
        assert ne0.status is WorkflowNodeExecutionStatus.COMPLETED
        ne1 = loaded.node_executions[1]
        assert ne1.node_id == "task-1"
        assert ne1.task_id == "task-xyz"

    @pytest.mark.unit
    async def test_get_not_found(
        self,
        repo: SQLiteWorkflowExecutionRepository,
    ) -> None:
        assert await repo.get("nonexistent") is None

    @pytest.mark.unit
    async def test_completed_with_timestamp(
        self,
        repo: SQLiteWorkflowExecutionRepository,
    ) -> None:
        now = datetime.now(UTC)
        exe = _make_execution(
            status=WorkflowExecutionStatus.COMPLETED,
            completed_at=now,
        )
        await repo.save(exe)
        loaded = await repo.get("wfexec-test001")
        assert loaded is not None
        assert loaded.completed_at is not None
        assert loaded.status is WorkflowExecutionStatus.COMPLETED

    @pytest.mark.unit
    async def test_failed_with_error(
        self,
        repo: SQLiteWorkflowExecutionRepository,
    ) -> None:
        exe = _make_execution(
            status=WorkflowExecutionStatus.FAILED,
            error="Something went wrong",
            completed_at=datetime.now(UTC),
        )
        await repo.save(exe)
        loaded = await repo.get("wfexec-test001")
        assert loaded is not None
        assert loaded.error == "Something went wrong"


class TestVersionConflict:
    """Optimistic concurrency control."""

    @pytest.mark.unit
    async def test_version_conflict_on_update(
        self,
        repo: SQLiteWorkflowExecutionRepository,
    ) -> None:
        exe = _make_execution()
        await repo.save(exe)

        # Try to save with version 3 (expected current version 2, but it's 1)
        stale = WorkflowExecution.model_validate(
            {**exe.model_dump(mode="json"), "version": 3},
        )
        with pytest.raises(VersionConflictError):
            await repo.save(stale)

    @pytest.mark.unit
    async def test_version_increment_succeeds(
        self,
        repo: SQLiteWorkflowExecutionRepository,
    ) -> None:
        exe = _make_execution()
        await repo.save(exe)

        updated = WorkflowExecution.model_validate(
            {
                **exe.model_dump(mode="json"),
                "version": 2,
                "status": "completed",
                "completed_at": datetime.now(UTC).isoformat(),
            },
        )
        await repo.save(updated)
        loaded = await repo.get("wfexec-test001")
        assert loaded is not None
        assert loaded.version == 2
        assert loaded.status is WorkflowExecutionStatus.COMPLETED


class TestListByDefinition:
    """List executions by definition ID."""

    @pytest.mark.unit
    async def test_list_by_definition(
        self,
        repo: SQLiteWorkflowExecutionRepository,
    ) -> None:
        older = datetime(2026, 1, 1, tzinfo=UTC)
        newer = datetime(2026, 1, 2, tzinfo=UTC)
        await repo.save(
            _make_execution(
                "wfexec-001",
                definition_id="wfdef-a",
                updated_at=older,
            ),
        )
        await repo.save(
            _make_execution(
                "wfexec-002",
                definition_id="wfdef-a",
                updated_at=newer,
            ),
        )
        await repo.save(_make_execution("wfexec-003", definition_id="wfdef-b"))

        results = await repo.list_by_definition("wfdef-a")
        assert len(results) == 2
        assert all(e.definition_id == "wfdef-a" for e in results)
        # Must be ordered by updated_at descending
        assert results[0].id == "wfexec-002"
        assert results[1].id == "wfexec-001"

    @pytest.mark.unit
    async def test_list_by_definition_empty(
        self,
        repo: SQLiteWorkflowExecutionRepository,
    ) -> None:
        results = await repo.list_by_definition("nonexistent")
        assert results == ()


class TestListByStatus:
    """List executions by status."""

    @pytest.mark.unit
    async def test_list_by_status(
        self,
        repo: SQLiteWorkflowExecutionRepository,
    ) -> None:
        older = datetime(2026, 1, 1, tzinfo=UTC)
        newer = datetime(2026, 1, 2, tzinfo=UTC)
        await repo.save(
            _make_execution(
                "wfexec-001",
                status=WorkflowExecutionStatus.RUNNING,
                updated_at=older,
            ),
        )
        await repo.save(
            _make_execution(
                "wfexec-002",
                status=WorkflowExecutionStatus.COMPLETED,
                completed_at=datetime.now(UTC),
            ),
        )
        await repo.save(
            _make_execution(
                "wfexec-003",
                status=WorkflowExecutionStatus.RUNNING,
                updated_at=newer,
            ),
        )

        results = await repo.list_by_status(WorkflowExecutionStatus.RUNNING)
        assert len(results) == 2
        assert all(e.status is WorkflowExecutionStatus.RUNNING for e in results)
        # Must be ordered by updated_at descending
        assert results[0].id == "wfexec-003"
        assert results[1].id == "wfexec-001"


class TestDelete:
    """Delete workflow executions."""

    @pytest.mark.unit
    async def test_delete_existing(
        self,
        repo: SQLiteWorkflowExecutionRepository,
    ) -> None:
        await repo.save(_make_execution())
        assert await repo.delete("wfexec-test001") is True
        assert await repo.get("wfexec-test001") is None

    @pytest.mark.unit
    async def test_delete_not_found(
        self,
        repo: SQLiteWorkflowExecutionRepository,
    ) -> None:
        assert await repo.delete("nonexistent") is False
