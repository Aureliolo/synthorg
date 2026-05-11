"""Conformance tests for workflow definition repository implementations.

Tests run against both SQLite and Postgres backends via the ``backend``
parametrized fixture.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import WorkflowEdgeType, WorkflowNodeType, WorkflowType
from synthorg.core.persistence_errors import PersistenceVersionConflictError
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from synthorg.persistence.protocol import PersistenceBackend


def _make_workflow_definition(
    definition_id: str = "wf-test",
    name: str = "Test Workflow",
    **overrides: object,
) -> WorkflowDefinition:
    """Build a valid WorkflowDefinition with START, TASK, and END nodes.

    The minimal valid graph is: START -> TASK -> END.
    """
    defaults: dict[str, object] = {
        "id": definition_id,
        "name": name,
        "description": "Test workflow",
        "workflow_type": WorkflowType.SEQUENTIAL_PIPELINE,
        "nodes": (
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
        "edges": (
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
        "created_by": "admin",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "revision": 1,
    }
    defaults.update(overrides)
    return WorkflowDefinition.model_validate(defaults)


@pytest.mark.integration
class TestWorkflowDefinitionRepository:
    """Conformance tests for WorkflowDefinitionRepository."""

    async def test_save_and_get_workflow_definition(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Save and retrieve a workflow definition."""
        repo = backend.workflow_definitions
        defn = _make_workflow_definition(
            definition_id="wf-001",
            name="Test Workflow",
            description="A test workflow",
        )

        await repo.save(defn)
        retrieved = await repo.get("wf-001")

        assert retrieved is not None
        assert retrieved.id == "wf-001"
        assert retrieved.name == "Test Workflow"
        assert len(retrieved.nodes) == 3
        assert len(retrieved.edges) == 2

    async def test_get_nonexistent_workflow_definition(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Get a non-existent workflow definition returns None."""
        repo = backend.workflow_definitions
        result = await repo.get("nonexistent-id")
        assert result is None

    async def test_list_definitions_empty(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """List definitions on empty repository returns empty tuple."""
        repo = backend.workflow_definitions
        definitions = await repo.list_definitions()
        assert len(definitions) == 0

    async def test_list_definitions_with_filter(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """List definitions filters by workflow type."""
        repo = backend.workflow_definitions
        defn1 = _make_workflow_definition(
            definition_id="wf-sequential",
            name="Sequential",
            workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        )
        defn2 = _make_workflow_definition(
            definition_id="wf-parallel",
            name="Parallel",
            workflow_type=WorkflowType.PARALLEL_EXECUTION,
        )

        await repo.save(defn1)
        await repo.save(defn2)

        sequential_only = await repo.list_definitions(
            workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        )
        assert len(sequential_only) >= 1
        assert any(d.id == "wf-sequential" for d in sequential_only)
        # Strict filter enforcement: the non-matching parallel
        # definition must not leak through, and every returned row
        # must have the requested type.  Otherwise a backend that
        # ignored the filter would silently pass this test.
        assert all(
            d.workflow_type == WorkflowType.SEQUENTIAL_PIPELINE for d in sequential_only
        )
        assert not any(d.id == "wf-parallel" for d in sequential_only)

    async def test_list_definitions_respects_limit(
        self,
        backend: PersistenceBackend,
    ) -> None:
        repo = backend.workflow_definitions
        for i in range(5):
            await repo.save(_make_workflow_definition(definition_id=f"wf-lim-{i}"))

        rows = await repo.list_definitions(limit=3)
        assert len(rows) <= 3

    async def test_update_workflow_definition(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Update a workflow definition with incremented version."""
        repo = backend.workflow_definitions
        defn = _make_workflow_definition(
            definition_id="wf-002",
            name="Original",
        )
        await repo.save(defn)

        updated = _make_workflow_definition(
            definition_id="wf-002",
            name="Updated",
            description="Updated description",
            workflow_type=WorkflowType.PARALLEL_EXECUTION,
            revision=2,
        )
        await repo.save(updated)

        retrieved = await repo.get("wf-002")
        assert retrieved is not None
        assert retrieved.name == "Updated"
        assert retrieved.description == "Updated description"
        assert retrieved.revision == 2

    async def test_version_conflict_on_stale_update(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Version conflict error raised on stale update."""
        repo = backend.workflow_definitions
        defn = _make_workflow_definition(
            definition_id="wf-003",
            name="Original",
        )
        await repo.save(defn)

        # Try to update with wrong version
        stale = _make_workflow_definition(
            definition_id="wf-003",
            name="Stale",
            revision=5,
        )

        with pytest.raises(PersistenceVersionConflictError):
            await repo.save(stale)

    async def test_delete_workflow_definition(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Delete a workflow definition."""
        repo = backend.workflow_definitions
        defn = _make_workflow_definition(
            definition_id="wf-for-delete",
            name="To Delete",
        )
        await repo.save(defn)

        deleted = await repo.delete("wf-for-delete")
        assert deleted is True

        retrieved = await repo.get("wf-for-delete")
        assert retrieved is None

    async def test_delete_nonexistent_returns_false(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Delete non-existent definition returns False."""
        repo = backend.workflow_definitions
        deleted = await repo.delete("nonexistent")
        assert deleted is False
