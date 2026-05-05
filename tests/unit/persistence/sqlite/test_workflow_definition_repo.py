"""Tests for SQLiteWorkflowDefinitionRepository."""

from datetime import UTC, datetime

import aiosqlite
import pytest

from synthorg.core.enums import (
    WorkflowEdgeType,
    WorkflowNodeType,
    WorkflowType,
)
from synthorg.core.persistence_errors import PersistenceVersionConflictError
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from synthorg.persistence.sqlite.workflow_definition_repo import (
    SQLiteWorkflowDefinitionRepository,
)


@pytest.fixture
def repo(
    migrated_db: aiosqlite.Connection,
) -> SQLiteWorkflowDefinitionRepository:
    return SQLiteWorkflowDefinitionRepository(migrated_db)


def _make_nodes(
    *,
    prefix: str = "n",
) -> tuple[WorkflowNode, ...]:
    """Build a minimal START -> TASK -> END node set."""
    return (
        WorkflowNode(
            id=f"{prefix}-start",
            type=WorkflowNodeType.START,
            label="Start",
            position_x=0.0,
            position_y=0.0,
        ),
        WorkflowNode(
            id=f"{prefix}-task",
            type=WorkflowNodeType.TASK,
            label="Do work",
            position_x=100.0,
            position_y=0.0,
            config={"priority": "high"},
        ),
        WorkflowNode(
            id=f"{prefix}-end",
            type=WorkflowNodeType.END,
            label="End",
            position_x=200.0,
            position_y=0.0,
        ),
    )


def _make_edges(
    *,
    prefix: str = "n",
) -> tuple[WorkflowEdge, ...]:
    """Build edges matching the nodes from ``_make_nodes``."""
    return (
        WorkflowEdge(
            id=f"{prefix}-e1",
            source_node_id=f"{prefix}-start",
            target_node_id=f"{prefix}-task",
            type=WorkflowEdgeType.SEQUENTIAL,
        ),
        WorkflowEdge(
            id=f"{prefix}-e2",
            source_node_id=f"{prefix}-task",
            target_node_id=f"{prefix}-end",
            type=WorkflowEdgeType.SEQUENTIAL,
            label="done",
        ),
    )


_DEFAULT_TS = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)


def _make_definition(  # noqa: PLR0913
    *,
    definition_id: str = "wf-001",
    name: str = "Test Workflow",
    description: str = "A test workflow definition",
    workflow_type: WorkflowType = WorkflowType.SEQUENTIAL_PIPELINE,
    created_by: str = "test-user",
    created_at: datetime = _DEFAULT_TS,
    updated_at: datetime = _DEFAULT_TS,
    revision: int = 1,
    node_prefix: str = "n",
) -> WorkflowDefinition:
    """Build a complete ``WorkflowDefinition`` for testing."""
    return WorkflowDefinition(
        id=definition_id,
        name=name,
        description=description,
        workflow_type=workflow_type,
        nodes=_make_nodes(prefix=node_prefix),
        edges=_make_edges(prefix=node_prefix),
        created_by=created_by,
        created_at=created_at,
        updated_at=updated_at,
        revision=revision,
    )


@pytest.mark.unit
class TestSQLiteWorkflowDefinitionRepository:
    async def test_save_and_get_roundtrip(
        self,
        repo: SQLiteWorkflowDefinitionRepository,
    ) -> None:
        defn = _make_definition()
        await repo.save(defn)

        result = await repo.get("wf-001")

        assert result is not None
        assert result.id == defn.id
        assert result.name == defn.name
        assert result.description == defn.description
        assert result.workflow_type == defn.workflow_type
        assert result.created_by == defn.created_by
        assert result.revision == defn.revision
        assert result.version == defn.version
        assert len(result.nodes) == len(defn.nodes)
        assert len(result.edges) == len(defn.edges)
        for original, loaded in zip(defn.nodes, result.nodes, strict=True):
            assert loaded.id == original.id
            assert loaded.type == original.type
            assert loaded.label == original.label
            assert loaded.position_x == original.position_x
            assert loaded.position_y == original.position_y
            assert dict(loaded.config) == dict(original.config)
        for orig_edge, loaded_edge in zip(defn.edges, result.edges, strict=True):
            assert loaded_edge.id == orig_edge.id
            assert loaded_edge.source_node_id == orig_edge.source_node_id
            assert loaded_edge.target_node_id == orig_edge.target_node_id
            assert loaded_edge.type == orig_edge.type
            assert loaded_edge.label == orig_edge.label

    async def test_save_upsert_updates_existing(
        self,
        repo: SQLiteWorkflowDefinitionRepository,
    ) -> None:
        defn_v1 = _make_definition(revision=1)
        await repo.save(defn_v1)

        defn_v2 = _make_definition(
            name="Updated Workflow",
            description="Updated description",
            workflow_type=WorkflowType.PARALLEL_EXECUTION,
            updated_at=datetime(2026, 4, 2, 8, 0, 0, tzinfo=UTC),
            revision=2,
        )
        await repo.save(defn_v2)

        result = await repo.get("wf-001")
        assert result is not None
        assert result.name == "Updated Workflow"
        assert result.description == "Updated description"
        assert result.workflow_type == WorkflowType.PARALLEL_EXECUTION
        assert result.revision == 2

    async def test_save_rejects_on_revision_mismatch(
        self,
        repo: SQLiteWorkflowDefinitionRepository,
    ) -> None:
        defn_v1 = _make_definition(revision=1)
        await repo.save(defn_v1)

        # Attempt to save revision 3 (skipping 2) -- should raise
        defn_v3 = _make_definition(
            name="Skipped revision",
            revision=3,
        )
        with pytest.raises(PersistenceVersionConflictError, match="Version conflict"):
            await repo.save(defn_v3)

        # Original revision 1 should still be stored
        result = await repo.get("wf-001")
        assert result is not None
        assert result.revision == 1
        assert result.name == "Test Workflow"

    async def test_save_rejects_duplicate_revision_1(
        self,
        repo: SQLiteWorkflowDefinitionRepository,
    ) -> None:
        """Saving revision=1 twice must raise PersistenceVersionConflictError.

        Regression test: previously the ``definition.revision > 1``
        guard silently treated this as success.
        """
        defn_v1 = _make_definition(revision=1)
        await repo.save(defn_v1)

        # Re-save with revision=1 should conflict (existing is also 1)
        defn_v1_again = _make_definition(
            name="Duplicate",
            revision=1,
        )
        with pytest.raises(PersistenceVersionConflictError, match="Version conflict"):
            await repo.save(defn_v1_again)

        # Original should be unchanged
        result = await repo.get("wf-001")
        assert result is not None
        assert result.name == "Test Workflow"

    async def test_get_not_found(
        self,
        repo: SQLiteWorkflowDefinitionRepository,
    ) -> None:
        result = await repo.get("nonexistent-id")
        assert result is None

    async def test_list_definitions_all(
        self,
        repo: SQLiteWorkflowDefinitionRepository,
    ) -> None:
        defn_a = _make_definition(
            definition_id="wf-a",
            name="Workflow A",
            node_prefix="a",
        )
        defn_b = _make_definition(
            definition_id="wf-b",
            name="Workflow B",
            node_prefix="b",
        )
        await repo.save(defn_a)
        await repo.save(defn_b)

        results = await repo.list_definitions()
        assert len(results) == 2
        ids = {d.id for d in results}
        assert ids == {"wf-a", "wf-b"}

    async def test_list_definitions_with_filter(
        self,
        repo: SQLiteWorkflowDefinitionRepository,
    ) -> None:
        defn_seq = _make_definition(
            definition_id="wf-seq",
            workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
            node_prefix="s",
        )
        defn_kanban = _make_definition(
            definition_id="wf-kanban",
            workflow_type=WorkflowType.KANBAN,
            node_prefix="k",
        )
        await repo.save(defn_seq)
        await repo.save(defn_kanban)

        results = await repo.list_definitions(
            workflow_type=WorkflowType.KANBAN,
        )
        assert len(results) == 1
        assert results[0].id == "wf-kanban"
        assert results[0].workflow_type == WorkflowType.KANBAN

    async def test_delete_returns_true(
        self,
        repo: SQLiteWorkflowDefinitionRepository,
    ) -> None:
        defn = _make_definition()
        await repo.save(defn)

        deleted = await repo.delete("wf-001")
        assert deleted is True

        result = await repo.get("wf-001")
        assert result is None

    async def test_delete_not_found_returns_false(
        self,
        repo: SQLiteWorkflowDefinitionRepository,
    ) -> None:
        deleted = await repo.delete("nonexistent-id")
        assert deleted is False

    async def test_timestamps_preserved_utc(
        self,
        repo: SQLiteWorkflowDefinitionRepository,
    ) -> None:
        created = datetime(2026, 1, 15, 9, 30, 45, tzinfo=UTC)
        updated = datetime(2026, 3, 20, 14, 0, 0, tzinfo=UTC)
        defn = _make_definition(created_at=created, updated_at=updated)
        await repo.save(defn)

        result = await repo.get("wf-001")
        assert result is not None
        assert result.created_at == created
        assert result.updated_at == updated
        assert result.created_at.tzinfo is not None
        assert result.updated_at.tzinfo is not None
