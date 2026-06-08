"""Tests for subworkflow-aware validation.

Covers :func:`validate_subworkflow_io` (save-time I/O contract checks)
and :func:`validate_subworkflow_graph` (static reference cycle
detection across the subworkflow registry).
"""

from datetime import UTC, datetime

import pytest

from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIODeclaration,
    WorkflowNode,
)
from synthorg.engine.workflow.enums import (
    WorkflowEdgeType,
    WorkflowNodeType,
    WorkflowType,
    WorkflowValueType,
)
from synthorg.engine.workflow.subworkflow_registry import SubworkflowRegistry
from synthorg.engine.workflow.validation import (
    ValidationErrorCode,
    validate_subworkflow_graph,
    validate_subworkflow_io,
)
from tests.unit.engine.workflow.test_subworkflow_registry import (
    FakeSubworkflowRepository,
)

_DEFAULT_TS = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)


def _make_minimal_definition(  # noqa: PLR0913
    *,
    definition_id: str,
    nodes: tuple[WorkflowNode, ...],
    edges: tuple[WorkflowEdge, ...],
    is_subworkflow: bool = False,
    version: str = "1.0.0",
    inputs: tuple[WorkflowIODeclaration, ...] = (),
    outputs: tuple[WorkflowIODeclaration, ...] = (),
) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=definition_id,
        name=definition_id,
        description="",
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        version=version,
        inputs=inputs,
        outputs=outputs,
        is_subworkflow=is_subworkflow,
        nodes=nodes,
        edges=edges,
        created_by="test-user",
        created_at=_DEFAULT_TS,
        updated_at=_DEFAULT_TS,
    )


def _make_child_subworkflow(
    *,
    subworkflow_id: str = "child-sub",
    version: str = "1.0.0",
    inputs: tuple[WorkflowIODeclaration, ...] = (),
    outputs: tuple[WorkflowIODeclaration, ...] = (),
    nested_refs: tuple[tuple[str, str], ...] = (),
) -> WorkflowDefinition:
    nodes: list[WorkflowNode] = [
        WorkflowNode(id="start", type=WorkflowNodeType.START, label="Start"),
    ]
    edges: list[WorkflowEdge] = []
    prev_id = "start"

    for idx, (nested_id, nested_version) in enumerate(nested_refs):
        node_id = f"nested-{idx}"
        nodes.append(
            WorkflowNode(
                id=node_id,
                type=WorkflowNodeType.SUBWORKFLOW,
                label=f"Nested {idx}",
                config={
                    "subworkflow_id": nested_id,
                    "version": nested_version,
                    "input_bindings": {},
                    "output_bindings": {},
                },
            ),
        )
        edges.append(
            WorkflowEdge(
                id=f"e-{idx}",
                source_node_id=prev_id,
                target_node_id=node_id,
                type=WorkflowEdgeType.SEQUENTIAL,
            ),
        )
        prev_id = node_id

    nodes.append(
        WorkflowNode(id="end", type=WorkflowNodeType.END, label="End"),
    )
    edges.append(
        WorkflowEdge(
            id="e-end",
            source_node_id=prev_id,
            target_node_id="end",
            type=WorkflowEdgeType.SEQUENTIAL,
        ),
    )

    return _make_minimal_definition(
        definition_id=subworkflow_id,
        nodes=tuple(nodes),
        edges=tuple(edges),
        is_subworkflow=True,
        version=version,
        inputs=inputs,
        outputs=outputs,
    )


def _make_parent_with_subworkflow(
    *,
    subworkflow_id: str = "child-sub",
    version: str = "1.0.0",
    input_bindings: dict[str, object] | None = None,
    output_bindings: dict[str, object] | None = None,
    parent_id: str = "parent-1",
) -> WorkflowDefinition:
    config: dict[str, object] = {
        "subworkflow_id": subworkflow_id,
        "version": version,
        "input_bindings": input_bindings or {},
        "output_bindings": output_bindings or {},
    }
    nodes = (
        WorkflowNode(id="start", type=WorkflowNodeType.START, label="Start"),
        WorkflowNode(
            id="sub-call",
            type=WorkflowNodeType.SUBWORKFLOW,
            label="Call",
            config=config,
        ),
        WorkflowNode(id="end", type=WorkflowNodeType.END, label="End"),
    )
    edges = (
        WorkflowEdge(
            id="e1",
            source_node_id="start",
            target_node_id="sub-call",
            type=WorkflowEdgeType.SEQUENTIAL,
        ),
        WorkflowEdge(
            id="e2",
            source_node_id="sub-call",
            target_node_id="end",
            type=WorkflowEdgeType.SEQUENTIAL,
        ),
    )
    return _make_minimal_definition(
        definition_id=parent_id,
        nodes=nodes,
        edges=edges,
    )


@pytest.fixture
def registry() -> SubworkflowRegistry:
    return SubworkflowRegistry(FakeSubworkflowRepository())


@pytest.mark.unit
class TestValidateSubworkflowIO:
    async def test_valid_io_contract(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        child = _make_child_subworkflow(
            inputs=(
                WorkflowIODeclaration(
                    name="quarter",
                    type=WorkflowValueType.STRING,
                ),
            ),
            outputs=(
                WorkflowIODeclaration(
                    name="report",
                    type=WorkflowValueType.STRING,
                ),
            ),
        )
        await registry.register(child)

        parent = _make_parent_with_subworkflow(
            input_bindings={"quarter": "@parent.current_quarter"},
            output_bindings={"report": "@child.report"},
        )
        result = await validate_subworkflow_io(parent, registry)
        assert result.valid

    async def test_missing_required_input_reported(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        child = _make_child_subworkflow(
            inputs=(
                WorkflowIODeclaration(
                    name="quarter",
                    type=WorkflowValueType.STRING,
                ),
            ),
        )
        await registry.register(child)

        parent = _make_parent_with_subworkflow(input_bindings={})
        result = await validate_subworkflow_io(parent, registry)
        assert not result.valid
        codes = {e.code for e in result.errors}
        assert ValidationErrorCode.SUBWORKFLOW_INPUT_MISSING in codes

    async def test_unknown_input_reported(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        child = _make_child_subworkflow()
        await registry.register(child)

        parent = _make_parent_with_subworkflow(
            input_bindings={"ghost": 42},
        )
        result = await validate_subworkflow_io(parent, registry)
        assert not result.valid
        codes = {e.code for e in result.errors}
        assert ValidationErrorCode.SUBWORKFLOW_INPUT_UNKNOWN in codes

    async def test_unknown_output_reported(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        child = _make_child_subworkflow()
        await registry.register(child)

        parent = _make_parent_with_subworkflow(
            output_bindings={"ghost": 42},
        )
        result = await validate_subworkflow_io(parent, registry)
        assert not result.valid
        codes = {e.code for e in result.errors}
        assert ValidationErrorCode.SUBWORKFLOW_OUTPUT_UNKNOWN in codes

    async def test_literal_type_mismatch_reported(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        child = _make_child_subworkflow(
            inputs=(
                WorkflowIODeclaration(
                    name="quarter",
                    type=WorkflowValueType.INTEGER,
                ),
            ),
        )
        await registry.register(child)

        parent = _make_parent_with_subworkflow(
            input_bindings={"quarter": "not an int"},
        )
        result = await validate_subworkflow_io(parent, registry)
        assert not result.valid

    async def test_dotted_expression_deferred_to_runtime(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        """Dotted-path expressions are not type-checked at save time."""
        child = _make_child_subworkflow(
            inputs=(
                WorkflowIODeclaration(
                    name="quarter",
                    type=WorkflowValueType.INTEGER,
                ),
            ),
        )
        await registry.register(child)

        parent = _make_parent_with_subworkflow(
            input_bindings={"quarter": "@parent.current_quarter"},
        )
        result = await validate_subworkflow_io(parent, registry)
        assert result.valid

    async def test_missing_config_reported(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        parent = _make_parent_with_subworkflow(subworkflow_id="")
        # Overwrite sub-call config to be invalid
        new_nodes = []
        for node in parent.nodes:
            if node.id == "sub-call":
                new_nodes.append(
                    WorkflowNode(
                        id=node.id,
                        type=node.type,
                        label=node.label,
                        config={},
                    ),
                )
            else:
                new_nodes.append(node)
        parent = parent.model_copy(update={"nodes": tuple(new_nodes)})

        result = await validate_subworkflow_io(parent, registry)
        codes = {e.code for e in result.errors}
        assert ValidationErrorCode.SUBWORKFLOW_REF_MISSING in codes

    async def test_unresolved_reference_reported(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        parent = _make_parent_with_subworkflow(
            subworkflow_id="sub-missing",
            version="1.0.0",
        )
        result = await validate_subworkflow_io(parent, registry)
        codes = {e.code for e in result.errors}
        assert ValidationErrorCode.SUBWORKFLOW_NOT_FOUND in codes


@pytest.mark.unit
class TestValidateSubworkflowGraph:
    async def test_no_subworkflows_is_acyclic(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        definition = _make_minimal_definition(
            definition_id="simple-parent",
            nodes=(
                WorkflowNode(
                    id="s",
                    type=WorkflowNodeType.START,
                    label="Start",
                ),
                WorkflowNode(
                    id="t",
                    type=WorkflowNodeType.TASK,
                    label="T",
                    config={"title": "T", "task_type": "admin"},
                ),
                WorkflowNode(
                    id="e",
                    type=WorkflowNodeType.END,
                    label="End",
                ),
            ),
            edges=(
                WorkflowEdge(
                    id="e1",
                    source_node_id="s",
                    target_node_id="t",
                    type=WorkflowEdgeType.SEQUENTIAL,
                ),
                WorkflowEdge(
                    id="e2",
                    source_node_id="t",
                    target_node_id="e",
                    type=WorkflowEdgeType.SEQUENTIAL,
                ),
            ),
        )
        result = await validate_subworkflow_graph(definition, registry)
        assert result.valid

    async def test_linear_chain_acyclic(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        leaf = _make_child_subworkflow(
            subworkflow_id="leaf",
            version="1.0.0",
        )
        await registry.register(leaf)
        middle = _make_child_subworkflow(
            subworkflow_id="middle",
            version="1.0.0",
            nested_refs=(("leaf", "1.0.0"),),
        )
        await registry.register(middle)
        parent = _make_parent_with_subworkflow(
            subworkflow_id="middle",
            version="1.0.0",
        )
        result = await validate_subworkflow_graph(parent, registry)
        assert result.valid

    async def test_two_subworkflows_cycle_detected(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        """A true two-node cycle is detected.

        Immutable versioning means cycles cannot be produced through the
        normal ``register`` API (the second definition in a loop would
        need to reference a future definition).  To exercise the DFS
        cycle detector, the test mutates the fake repository's internal
        state directly -- simulating a corrupt write that bypassed save-
        time validation or a broken migration.  The DFS must still
        report the cycle as a safety net.
        """
        a_v1 = _make_child_subworkflow(
            subworkflow_id="sub-a",
            version="1.0.0",
            nested_refs=(("sub-b", "1.0.0"),),
        )
        # sub-b references sub-a@1.0.0 -> after mutation, sub-a@1.0.0
        # will reference sub-b@1.0.0, closing the loop.
        b = _make_child_subworkflow(
            subworkflow_id="sub-b",
            version="1.0.0",
            nested_refs=(("sub-a", "1.0.0"),),
        )

        # Inject both directly into the fake repo (bypasses validation).
        fake_repo = registry._repo
        assert isinstance(fake_repo, FakeSubworkflowRepository)
        fake_repo._rows[("sub-a", "1.0.0")] = a_v1
        fake_repo._rows[("sub-b", "1.0.0")] = b

        parent = _make_parent_with_subworkflow(
            subworkflow_id="sub-a",
            version="1.0.0",
        )
        result = await validate_subworkflow_graph(parent, registry)
        codes = {e.code for e in result.errors}
        assert ValidationErrorCode.SUBWORKFLOW_CYCLE_DETECTED in codes

    async def test_three_deep_cycle_detected(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        """A 3-node cycle is detected via direct repo injection."""
        a = _make_child_subworkflow(
            subworkflow_id="sub-a",
            version="1.0.0",
            nested_refs=(("sub-b", "1.0.0"),),
        )
        b = _make_child_subworkflow(
            subworkflow_id="sub-b",
            version="1.0.0",
            nested_refs=(("sub-c", "1.0.0"),),
        )
        c = _make_child_subworkflow(
            subworkflow_id="sub-c",
            version="1.0.0",
            nested_refs=(("sub-a", "1.0.0"),),
        )
        fake_repo = registry._repo
        assert isinstance(fake_repo, FakeSubworkflowRepository)
        fake_repo._rows[("sub-a", "1.0.0")] = a
        fake_repo._rows[("sub-b", "1.0.0")] = b
        fake_repo._rows[("sub-c", "1.0.0")] = c

        parent = _make_parent_with_subworkflow(
            subworkflow_id="sub-a",
            version="1.0.0",
        )
        result = await validate_subworkflow_graph(parent, registry)
        codes = {e.code for e in result.errors}
        assert ValidationErrorCode.SUBWORKFLOW_CYCLE_DETECTED in codes
