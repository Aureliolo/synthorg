"""Integration: subworkflow registry, execution, and persistence round-trips.

Tests exercise the full stack with real SQLite persistence (via yoyo
migrations), verifying that subworkflow registration, versioning,
parent tracking, and execution produce correct results end-to-end.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from synthorg.core.enums import (
    WorkflowNodeType,
    WorkflowType,
    WorkflowValueType,
)
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIODeclaration,
    WorkflowNode,
)
from synthorg.engine.workflow.subworkflow_registry import SubworkflowRegistry
from synthorg.engine.workflow.yaml_export import export_workflow_yaml
from synthorg.persistence import migrations
from synthorg.persistence.sqlite.subworkflow_repo import (
    SQLiteSubworkflowRepository,
)
from synthorg.persistence.sqlite.workflow_definition_repo import (
    SQLiteWorkflowDefinitionRepository,
)
from tests._shared.persistence import make_private_write_context

_TS = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
async def db(tmp_path: Path) -> AsyncGenerator[aiosqlite.Connection]:
    """Temp-file SQLite with yoyo migrations applied."""
    db_path = tmp_path / "test.db"
    rev_path = migrations.copy_revisions(tmp_path / "revisions")
    await migrations.migrate_apply(
        migrations.to_sqlite_url(str(db_path)),
        revisions_path=rev_path,
    )
    conn = await aiosqlite.connect(str(db_path))
    try:
        conn.row_factory = aiosqlite.Row
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def sub_repo(db: aiosqlite.Connection) -> SQLiteSubworkflowRepository:
    return SQLiteSubworkflowRepository(db, write_context=make_private_write_context())


@pytest.fixture
def def_repo(db: aiosqlite.Connection) -> SQLiteWorkflowDefinitionRepository:
    return SQLiteWorkflowDefinitionRepository(
        db, write_context=make_private_write_context()
    )


@pytest.fixture
def registry(
    sub_repo: SQLiteSubworkflowRepository,
) -> SubworkflowRegistry:
    return SubworkflowRegistry(sub_repo)


# ── Helpers ─────────��───────────────────────��───────────────────


def _make_child(
    *,
    subworkflow_id: str = "sub-greet",
    version: str = "1.0.0",
) -> WorkflowDefinition:
    """A simple subworkflow with one TASK node."""
    return WorkflowDefinition(
        id=subworkflow_id,
        name="Greeting Subworkflow",
        description="Greets a user by name",
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        version=version,
        is_subworkflow=True,
        inputs=(
            WorkflowIODeclaration(
                name="user_name",
                type=WorkflowValueType.STRING,
                required=True,
            ),
        ),
        outputs=(
            WorkflowIODeclaration(
                name="greeting",
                type=WorkflowValueType.STRING,
                required=True,
            ),
        ),
        nodes=(
            WorkflowNode(
                id="start",
                type=WorkflowNodeType.START,
                label="Start",
            ),
            WorkflowNode(
                id="greet-task",
                type=WorkflowNodeType.TASK,
                label="Greet",
                config={"title": "Greet user"},
            ),
            WorkflowNode(
                id="end",
                type=WorkflowNodeType.END,
                label="End",
            ),
        ),
        edges=(
            WorkflowEdge(
                id="e1",
                source_node_id="start",
                target_node_id="greet-task",
            ),
            WorkflowEdge(
                id="e2",
                source_node_id="greet-task",
                target_node_id="end",
            ),
        ),
        created_by="test",
        created_at=_TS,
        updated_at=_TS,
    )


def _make_parent(
    *,
    definition_id: str = "wf-parent",
    child_id: str = "sub-greet",
    child_version: str = "1.0.0",
) -> WorkflowDefinition:
    """A parent workflow with START -> SUBWORKFLOW -> END."""
    return WorkflowDefinition(
        id=definition_id,
        name="Parent Workflow",
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        nodes=(
            WorkflowNode(
                id="start",
                type=WorkflowNodeType.START,
                label="Start",
            ),
            WorkflowNode(
                id="sub-call",
                type=WorkflowNodeType.SUBWORKFLOW,
                label="Call Child",
                config={
                    "subworkflow_id": child_id,
                    "version": child_version,
                    "input_bindings": {"user_name": "Alice"},
                    "output_bindings": {"greeting": "@child.greeting"},
                },
            ),
            WorkflowNode(
                id="end",
                type=WorkflowNodeType.END,
                label="End",
            ),
        ),
        edges=(
            WorkflowEdge(
                id="e1",
                source_node_id="start",
                target_node_id="sub-call",
            ),
            WorkflowEdge(
                id="e2",
                source_node_id="sub-call",
                target_node_id="end",
            ),
        ),
        created_by="test",
        created_at=_TS,
        updated_at=_TS,
    )


# ── Tests ───────────────────────────────────────────────────────


@pytest.mark.integration
class TestSubworkflowVersioning:
    """Registry versioning and parent tracking with real SQLite."""

    async def test_register_multiple_versions_and_list(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        """Register two versions and verify list order."""
        await registry.register(_make_child(version="1.0.0"))
        await registry.register(_make_child(version="2.0.0"))

        versions = await registry.list_versions("sub-greet")
        assert versions == ("2.0.0", "1.0.0")

        summaries = await registry.list_all()
        assert len(summaries) == 1
        assert summaries[0].latest_version == "2.0.0"
        assert summaries[0].version_count == 2

    async def test_new_version_does_not_break_parents_on_old_version(
        self,
        registry: SubworkflowRegistry,
        def_repo: SQLiteWorkflowDefinitionRepository,
    ) -> None:
        """Publishing v2 does not affect parents pinning v1."""
        await registry.register(_make_child(version="1.0.0"))
        await registry.register(_make_child(version="2.0.0"))

        parent = _make_parent(child_version="1.0.0")
        await def_repo.save(parent)

        # Parent still resolves v1.0.0
        child = await registry.get("sub-greet", "1.0.0")
        assert child is not None
        assert child.version == "1.0.0"

    async def test_explicit_update_repins_parent_to_new_version(
        self,
        registry: SubworkflowRegistry,
        def_repo: SQLiteWorkflowDefinitionRepository,
    ) -> None:
        """Updating parent's subworkflow node config repins to v2."""
        await registry.register(_make_child(version="1.0.0"))
        await registry.register(_make_child(version="2.0.0"))

        parent_v1 = _make_parent(child_version="1.0.0")
        await def_repo.save(parent_v1)

        # Re-pin to v2.0.0
        repinned = parent_v1.model_copy(
            update={
                "nodes": tuple(
                    n.model_copy(
                        update={
                            "config": {
                                **dict(n.config),
                                "version": "2.0.0",
                            },
                        },
                    )
                    if n.type is WorkflowNodeType.SUBWORKFLOW
                    else n
                    for n in parent_v1.nodes
                ),
                "revision": 2,
            },
        )
        await def_repo.save(repinned)

        loaded = await def_repo.get("wf-parent")
        assert loaded is not None
        sub_node = next(
            n for n in loaded.nodes if n.type is WorkflowNodeType.SUBWORKFLOW
        )
        assert sub_node.config["version"] == "2.0.0"


@pytest.mark.integration
class TestSubworkflowDeleteProtection:
    """Atomic delete protection with real SQLite transactions."""

    async def test_delete_blocked_when_parent_pins(
        self,
        registry: SubworkflowRegistry,
        def_repo: SQLiteWorkflowDefinitionRepository,
    ) -> None:
        """Cannot delete a subworkflow version pinned by a parent."""
        await registry.register(_make_child(version="1.0.0"))
        parent = _make_parent(child_version="1.0.0")
        await def_repo.save(parent)

        from synthorg.engine.errors import SubworkflowIOError

        with pytest.raises(SubworkflowIOError, match="still referenced"):
            await registry.delete("sub-greet", "1.0.0")

    async def test_delete_succeeds_when_no_parents(
        self,
        registry: SubworkflowRegistry,
        sub_repo: SQLiteSubworkflowRepository,
    ) -> None:
        """Delete succeeds when no parent references the version."""
        await registry.register(_make_child(version="1.0.0"))
        await registry.delete("sub-greet", "1.0.0")

        # Use repo directly since registry.get() raises on not-found.
        result = await sub_repo.get("sub-greet", "1.0.0")
        assert result is None


@pytest.mark.integration
class TestSubworkflowYamlRoundTrip:
    """YAML export round-trip with real SQLite persistence."""

    async def test_yaml_round_trip_with_subworkflows(
        self,
        registry: SubworkflowRegistry,
        def_repo: SQLiteWorkflowDefinitionRepository,
    ) -> None:
        """Export a parent with a SUBWORKFLOW node and verify YAML content."""
        await registry.register(_make_child(version="1.0.0"))
        parent = _make_parent(child_version="1.0.0")
        await def_repo.save(parent)

        loaded = await def_repo.get("wf-parent")
        assert loaded is not None

        yaml_str = export_workflow_yaml(loaded)

        # Verify subworkflow fields are in the YAML output
        assert "subworkflow_id: sub-greet" in yaml_str
        assert "version: 1.0.0" in yaml_str or "version: '1.0.0'" in yaml_str
        assert "input_bindings" in yaml_str
        assert "output_bindings" in yaml_str
