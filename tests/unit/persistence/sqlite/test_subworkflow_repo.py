"""Tests for :class:`SQLiteSubworkflowRepository`."""

import json
from datetime import UTC, datetime

import aiosqlite
import pytest

from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIODeclaration,
    WorkflowNode,
)
from synthorg.engine.workflow.enums import (
    WorkflowNodeType,
    WorkflowType,
    WorkflowValueType,
)
from synthorg.persistence.sqlite.subworkflow_repo import (
    SQLiteSubworkflowRepository,
)
from tests._shared.persistence import make_private_write_context

_DEFAULT_TS = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def repo(
    migrated_db: aiosqlite.Connection,
) -> SQLiteSubworkflowRepository:
    return SQLiteSubworkflowRepository(
        migrated_db, write_context=make_private_write_context()
    )


def _make_nodes() -> tuple[WorkflowNode, ...]:
    return (
        WorkflowNode(
            id="start-1",
            type=WorkflowNodeType.START,
            label="Start",
        ),
        WorkflowNode(
            id="task-1",
            type=WorkflowNodeType.TASK,
            label="Do work",
            config={"title": "Do work", "task_type": "development"},
        ),
        WorkflowNode(
            id="end-1",
            type=WorkflowNodeType.END,
            label="End",
        ),
    )


def _make_edges() -> tuple[WorkflowEdge, ...]:
    return (
        WorkflowEdge(
            id="e1",
            source_node_id="start-1",
            target_node_id="task-1",
        ),
        WorkflowEdge(
            id="e2",
            source_node_id="task-1",
            target_node_id="end-1",
        ),
    )


def _make_subworkflow(  # noqa: PLR0913
    *,
    subworkflow_id: str = "sub-quarterly-close",
    version: str = "1.0.0",
    name: str = "Quarterly Close",
    description: str = "Quarterly finance close workflow",
    inputs: tuple[WorkflowIODeclaration, ...] = (),
    outputs: tuple[WorkflowIODeclaration, ...] = (),
) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=subworkflow_id,
        name=name,
        description=description,
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        version=version,
        inputs=inputs,
        outputs=outputs,
        is_subworkflow=True,
        nodes=_make_nodes(),
        edges=_make_edges(),
        created_by="test-user",
        created_at=_DEFAULT_TS,
        updated_at=_DEFAULT_TS,
    )


@pytest.mark.unit
class TestSaveAndGet:
    """Save and retrieve subworkflow rows."""

    async def test_save_and_get_roundtrip(
        self,
        repo: SQLiteSubworkflowRepository,
    ) -> None:
        sub = _make_subworkflow(
            inputs=(
                WorkflowIODeclaration(
                    name="quarter",
                    type=WorkflowValueType.STRING,
                ),
            ),
            outputs=(
                WorkflowIODeclaration(
                    name="closing_report",
                    type=WorkflowValueType.STRING,
                ),
            ),
        )
        await repo.save(sub)

        loaded = await repo.get(("sub-quarterly-close", "1.0.0"))
        assert loaded is not None
        assert loaded.id == sub.id
        assert loaded.version == "1.0.0"
        assert loaded.is_subworkflow is True
        assert len(loaded.inputs) == 1
        assert loaded.inputs[0].name == "quarter"
        assert loaded.inputs[0].type is WorkflowValueType.STRING
        assert len(loaded.outputs) == 1
        assert len(loaded.nodes) == 3
        assert len(loaded.edges) == 2

    async def test_get_missing_returns_none(
        self,
        repo: SQLiteSubworkflowRepository,
    ) -> None:
        result = await repo.get(("sub-nonexistent", "1.0.0"))
        assert result is None

    async def test_duplicate_version_rejected(
        self,
        repo: SQLiteSubworkflowRepository,
    ) -> None:
        sub = _make_subworkflow()
        await repo.save(sub)

        duplicate = _make_subworkflow(name="Different Name")
        with pytest.raises(DuplicateRecordError):
            await repo.save(duplicate)


@pytest.mark.unit
class TestListVersions:
    """List semver strings for a subworkflow."""

    async def test_list_versions_sorted_semver_desc(
        self,
        repo: SQLiteSubworkflowRepository,
    ) -> None:
        for version in ["1.0.0", "1.9.0", "1.10.0", "2.0.0"]:
            await repo.save(_make_subworkflow(version=version))

        versions = await repo.list_versions("sub-quarterly-close")
        assert versions == ("2.0.0", "1.10.0", "1.9.0", "1.0.0")

    async def test_list_versions_missing_subworkflow(
        self,
        repo: SQLiteSubworkflowRepository,
    ) -> None:
        versions = await repo.list_versions("sub-nonexistent")
        assert versions == ()


@pytest.mark.unit
class TestListSummariesAndSearch:
    """Summary and search endpoints."""

    async def test_list_summaries_reflects_latest(
        self,
        repo: SQLiteSubworkflowRepository,
    ) -> None:
        await repo.save(
            _make_subworkflow(version="1.0.0", name="Close v1.0"),
        )
        await repo.save(
            _make_subworkflow(
                version="1.10.0",
                name="Close v1.10",
                inputs=(
                    WorkflowIODeclaration(
                        name="quarter",
                        type=WorkflowValueType.STRING,
                    ),
                ),
            ),
        )

        summaries = await repo.list_summaries()
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.subworkflow_id == "sub-quarterly-close"
        assert summary.latest_version == "1.10.0"
        assert summary.name == "Close v1.10"
        assert summary.input_count == 1
        assert summary.output_count == 0
        assert summary.version_count == 2

    async def test_search_matches_name_case_insensitive(
        self,
        repo: SQLiteSubworkflowRepository,
    ) -> None:
        await repo.save(
            _make_subworkflow(
                subworkflow_id="sub-quarterly-close",
                name="Quarterly Close",
                description="Finance close workflow",
            ),
        )
        await repo.save(
            _make_subworkflow(
                subworkflow_id="sub-weekly-report",
                name="Weekly Report",
                description="Status summary",
            ),
        )

        results = await repo.search("QUARTERLY")
        assert len(results) == 1
        assert results[0].subworkflow_id == "sub-quarterly-close"

    async def test_search_escapes_like_wildcards(
        self,
        repo: SQLiteSubworkflowRepository,
    ) -> None:
        await repo.save(
            _make_subworkflow(
                subworkflow_id="sub-percent-literal",
                name="50% Complete Workflow",
                description="Uses percent sign",
            ),
        )
        await repo.save(
            _make_subworkflow(
                subworkflow_id="sub-underscore-literal",
                name="step_1 setup",
                description="Uses underscore",
            ),
        )

        results = await repo.search("50%")
        assert len(results) == 1
        assert results[0].subworkflow_id == "sub-percent-literal"

        results = await repo.search("step_1")
        assert len(results) == 1
        assert results[0].subworkflow_id == "sub-underscore-literal"


@pytest.mark.unit
class TestDelete:
    """Delete endpoint."""

    async def test_delete_existing_returns_true(
        self,
        repo: SQLiteSubworkflowRepository,
    ) -> None:
        await repo.save(_make_subworkflow())
        assert await repo.delete(("sub-quarterly-close", "1.0.0")) is True

    async def test_delete_missing_returns_false(
        self,
        repo: SQLiteSubworkflowRepository,
    ) -> None:
        assert await repo.delete(("sub-nonexistent", "1.0.0")) is False

    async def test_delete_version_leaves_other_versions_intact(
        self,
        repo: SQLiteSubworkflowRepository,
    ) -> None:
        await repo.save(_make_subworkflow(version="1.0.0"))
        await repo.save(_make_subworkflow(version="2.0.0"))

        await repo.delete(("sub-quarterly-close", "1.0.0"))
        remaining = await repo.list_versions("sub-quarterly-close")
        assert remaining == ("2.0.0",)


@pytest.mark.unit
class TestFindParents:
    """find_parents scans workflow_definitions for SUBWORKFLOW nodes."""

    async def test_find_parents_returns_references(
        self,
        repo: SQLiteSubworkflowRepository,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        # Insert a parent workflow directly into workflow_definitions
        # with a SUBWORKFLOW node referencing the subworkflow.
        parent_nodes = [
            {
                "id": "start-1",
                "type": "start",
                "label": "Start",
                "position_x": 0.0,
                "position_y": 0.0,
                "config": {},
            },
            {
                "id": "sub-node-1",
                "type": "subworkflow",
                "label": "Quarterly Close",
                "position_x": 100.0,
                "position_y": 100.0,
                "config": {
                    "subworkflow_id": "sub-quarterly-close",
                    "version": "1.0.0",
                    "input_bindings": {},
                    "output_bindings": {},
                },
            },
            {
                "id": "end-1",
                "type": "end",
                "label": "End",
                "position_x": 200.0,
                "position_y": 200.0,
                "config": {},
            },
        ]
        parent_edges = [
            {
                "id": "e1",
                "source_node_id": "start-1",
                "target_node_id": "sub-node-1",
                "type": "sequential",
                "label": None,
            },
            {
                "id": "e2",
                "source_node_id": "sub-node-1",
                "target_node_id": "end-1",
                "type": "sequential",
                "label": None,
            },
        ]
        await migrated_db.execute(
            """INSERT INTO workflow_definitions
               (id, name, description, workflow_type, version, inputs, outputs,
                is_subworkflow, nodes, edges, created_by, created_at, updated_at,
                revision)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "parent-1",
                "Parent Workflow",
                "",
                "sequential_pipeline",
                "1.0.0",
                "[]",
                "[]",
                0,
                json.dumps(parent_nodes),
                json.dumps(parent_edges),
                "test-user",
                _DEFAULT_TS.isoformat(),
                _DEFAULT_TS.isoformat(),
                1,
            ),
        )
        await migrated_db.commit()

        refs = await repo.find_parents("sub-quarterly-close", "1.0.0")
        assert len(refs) == 1
        ref = refs[0]
        assert ref.parent_id == "parent-1"
        assert ref.parent_name == "Parent Workflow"
        assert ref.pinned_version == "1.0.0"
        assert ref.node_id == "sub-node-1"

    async def test_find_parents_filters_by_version(
        self,
        repo: SQLiteSubworkflowRepository,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        parent_nodes = [
            {
                "id": "start-1",
                "type": "start",
                "label": "Start",
                "position_x": 0.0,
                "position_y": 0.0,
                "config": {},
            },
            {
                "id": "sub-node-1",
                "type": "subworkflow",
                "label": "Close",
                "position_x": 100.0,
                "position_y": 100.0,
                "config": {
                    "subworkflow_id": "sub-quarterly-close",
                    "version": "1.0.0",
                },
            },
            {
                "id": "end-1",
                "type": "end",
                "label": "End",
                "position_x": 0.0,
                "position_y": 0.0,
                "config": {},
            },
        ]
        await migrated_db.execute(
            """INSERT INTO workflow_definitions
               (id, name, description, workflow_type, version, inputs, outputs,
                is_subworkflow, nodes, edges, created_by, created_at, updated_at,
                revision)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "parent-old",
                "Parent Old",
                "",
                "sequential_pipeline",
                "1.0.0",
                "[]",
                "[]",
                0,
                json.dumps(parent_nodes),
                "[]",
                "test-user",
                _DEFAULT_TS.isoformat(),
                _DEFAULT_TS.isoformat(),
                1,
            ),
        )
        await migrated_db.commit()

        # Version-specific query should match
        refs_v1 = await repo.find_parents("sub-quarterly-close", "1.0.0")
        assert len(refs_v1) == 1

        # Non-matching version should return empty
        refs_v2 = await repo.find_parents("sub-quarterly-close", "2.0.0")
        assert len(refs_v2) == 0

        # None means "any version"
        refs_any = await repo.find_parents("sub-quarterly-close", None)
        assert len(refs_any) == 1

    async def test_find_parents_returns_parent_type_workflow_definition(
        self,
        repo: SQLiteSubworkflowRepository,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        """Parents from workflow_definitions have parent_type='workflow_definition'."""
        parent_nodes = [
            {
                "id": "start-1",
                "type": "start",
                "label": "Start",
                "position_x": 0.0,
                "position_y": 0.0,
                "config": {},
            },
            {
                "id": "sub-node-1",
                "type": "subworkflow",
                "label": "Child",
                "position_x": 100.0,
                "position_y": 100.0,
                "config": {
                    "subworkflow_id": "sub-quarterly-close",
                    "version": "1.0.0",
                    "input_bindings": {},
                    "output_bindings": {},
                },
            },
            {
                "id": "end-1",
                "type": "end",
                "label": "End",
                "position_x": 200.0,
                "position_y": 200.0,
                "config": {},
            },
        ]
        await migrated_db.execute(
            """INSERT INTO workflow_definitions
               (id, name, description, workflow_type, version, inputs, outputs,
                is_subworkflow, nodes, edges, created_by, created_at, updated_at,
                revision)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "parent-wf",
                "Parent WF",
                "",
                "sequential_pipeline",
                "1.0.0",
                "[]",
                "[]",
                0,
                json.dumps(parent_nodes),
                "[]",
                "test-user",
                _DEFAULT_TS.isoformat(),
                _DEFAULT_TS.isoformat(),
                1,
            ),
        )
        await migrated_db.commit()

        refs = await repo.find_parents("sub-quarterly-close", "1.0.0")
        assert len(refs) == 1
        assert refs[0].parent_type == "workflow_definition"

    async def test_find_parents_scans_subworkflows_table(
        self,
        repo: SQLiteSubworkflowRepository,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        """A subworkflow referencing another subworkflow is discovered."""
        # Insert a parent subworkflow that references child sub-quarterly-close
        parent_sub_nodes = [
            {
                "id": "start-1",
                "type": "start",
                "label": "Start",
                "position_x": 0.0,
                "position_y": 0.0,
                "config": {},
            },
            {
                "id": "sub-node-nested",
                "type": "subworkflow",
                "label": "Nested Child",
                "position_x": 100.0,
                "position_y": 100.0,
                "config": {
                    "subworkflow_id": "sub-quarterly-close",
                    "version": "1.0.0",
                    "input_bindings": {},
                    "output_bindings": {},
                },
            },
            {
                "id": "end-1",
                "type": "end",
                "label": "End",
                "position_x": 200.0,
                "position_y": 200.0,
                "config": {},
            },
        ]
        await migrated_db.execute(
            """INSERT INTO subworkflows
               (subworkflow_id, semver, name, description, workflow_type,
                inputs, outputs, nodes, edges, created_by, created_at,
                updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "sub-parent-wrapper",
                "1.0.0",
                "Parent Wrapper",
                "",
                "sequential_pipeline",
                "[]",
                "[]",
                json.dumps(parent_sub_nodes),
                "[]",
                "test-user",
                _DEFAULT_TS.isoformat(),
                _DEFAULT_TS.isoformat(),
            ),
        )
        await migrated_db.commit()

        refs = await repo.find_parents("sub-quarterly-close", "1.0.0")
        assert len(refs) == 1
        ref = refs[0]
        assert ref.parent_id == "sub-parent-wrapper"
        assert ref.parent_name == "Parent Wrapper"
        assert ref.pinned_version == "1.0.0"
        assert ref.node_id == "sub-node-nested"
        assert ref.parent_type == "subworkflow"

    async def test_find_parents_merges_both_tables(
        self,
        repo: SQLiteSubworkflowRepository,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        """Parents from both tables are returned together."""
        # Insert a workflow_definition parent
        wf_nodes = [
            {
                "id": "start-1",
                "type": "start",
                "label": "S",
                "position_x": 0.0,
                "position_y": 0.0,
                "config": {},
            },
            {
                "id": "sub-1",
                "type": "subworkflow",
                "label": "C",
                "position_x": 1.0,
                "position_y": 1.0,
                "config": {
                    "subworkflow_id": "sub-quarterly-close",
                    "version": "1.0.0",
                },
            },
            {
                "id": "end-1",
                "type": "end",
                "label": "E",
                "position_x": 2.0,
                "position_y": 2.0,
                "config": {},
            },
        ]
        await migrated_db.execute(
            """INSERT INTO workflow_definitions
               (id, name, description, workflow_type, version, inputs, outputs,
                is_subworkflow, nodes, edges, created_by, created_at, updated_at,
                revision)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "wf-parent",
                "WF Parent",
                "",
                "sequential_pipeline",
                "1.0.0",
                "[]",
                "[]",
                0,
                json.dumps(wf_nodes),
                "[]",
                "test-user",
                _DEFAULT_TS.isoformat(),
                _DEFAULT_TS.isoformat(),
                1,
            ),
        )
        # Insert a subworkflow parent
        sub_nodes = [
            {
                "id": "start-1",
                "type": "start",
                "label": "S",
                "position_x": 0.0,
                "position_y": 0.0,
                "config": {},
            },
            {
                "id": "sub-2",
                "type": "subworkflow",
                "label": "C",
                "position_x": 1.0,
                "position_y": 1.0,
                "config": {
                    "subworkflow_id": "sub-quarterly-close",
                    "version": "1.0.0",
                },
            },
            {
                "id": "end-1",
                "type": "end",
                "label": "E",
                "position_x": 2.0,
                "position_y": 2.0,
                "config": {},
            },
        ]
        await migrated_db.execute(
            """INSERT INTO subworkflows
               (subworkflow_id, semver, name, description, workflow_type,
                inputs, outputs, nodes, edges, created_by, created_at,
                updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "sub-outer",
                "2.0.0",
                "Outer Sub",
                "",
                "sequential_pipeline",
                "[]",
                "[]",
                json.dumps(sub_nodes),
                "[]",
                "test-user",
                _DEFAULT_TS.isoformat(),
                _DEFAULT_TS.isoformat(),
            ),
        )
        await migrated_db.commit()

        refs = await repo.find_parents("sub-quarterly-close", "1.0.0")
        assert len(refs) == 2
        types = {r.parent_type for r in refs}
        assert types == {"workflow_definition", "subworkflow"}
