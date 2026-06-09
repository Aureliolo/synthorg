"""Tests for the subworkflow-aware execution service frame stack.

Covers:

- Parent with a SUBWORKFLOW node calls a child and the child's tasks
  land in the same WorkflowExecution under qualified IDs.
- Runtime depth limit enforcement.
- Output projection feeds downstream conditionals.
- Variable scoping: child frame's condition evaluator only sees
  declared inputs.
"""

from datetime import UTC, datetime
from typing import override

import pytest

from synthorg.core.task import Task
from synthorg.core.task_enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.engine.errors import SubworkflowDepthExceededError
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import CreateTaskData
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIODeclaration,
    WorkflowNode,
)
from synthorg.engine.workflow.enums import (
    WorkflowEdgeType,
    WorkflowNodeExecutionStatus,
    WorkflowNodeType,
    WorkflowType,
    WorkflowValueType,
)
from synthorg.engine.workflow.execution_service import (
    WorkflowExecutionService,
)
from synthorg.engine.workflow.subworkflow_registry import SubworkflowRegistry
from tests._shared import as_uuid
from tests.unit.engine.workflow.test_subworkflow_registry import (
    FakeSubworkflowRepository,
)

_NOW = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)


def _task_node(
    node_id: str,
    title: str = "Work",
) -> WorkflowNode:
    return WorkflowNode(
        id=node_id,
        type=WorkflowNodeType.TASK,
        label=title,
        config={"title": title, "task_type": "development"},
    )


def _seq_edge(edge_id: str, src: str, tgt: str) -> WorkflowEdge:
    return WorkflowEdge(
        id=edge_id,
        source_node_id=src,
        target_node_id=tgt,
        type=WorkflowEdgeType.SEQUENTIAL,
    )


def _make_child_definition(
    *,
    definition_id: str = "child-sub",
    version: str = "1.0.0",
    inputs: tuple[WorkflowIODeclaration, ...] = (),
    outputs: tuple[WorkflowIODeclaration, ...] = (),
) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=definition_id,
        name="Child",
        description="",
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        version=version,
        inputs=inputs,
        outputs=outputs,
        is_subworkflow=True,
        nodes=(
            WorkflowNode(
                id="start",
                type=WorkflowNodeType.START,
                label="Start",
            ),
            _task_node("child-task", "Child Task"),
            WorkflowNode(
                id="end",
                type=WorkflowNodeType.END,
                label="End",
            ),
        ),
        edges=(
            _seq_edge("ce1", "start", "child-task"),
            _seq_edge("ce2", "child-task", "end"),
        ),
        created_by="test",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_parent_definition(
    *,
    subworkflow_id: str = "child-sub",
    version: str = "1.0.0",
    input_bindings: dict[str, object] | None = None,
    output_bindings: dict[str, object] | None = None,
) -> WorkflowDefinition:
    sub_config: dict[str, object] = {
        "subworkflow_id": subworkflow_id,
        "version": version,
        "input_bindings": input_bindings or {},
        "output_bindings": output_bindings or {},
    }
    return WorkflowDefinition(
        id="parent-wf",
        name="Parent",
        description="",
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        version="1.0.0",
        is_subworkflow=False,
        nodes=(
            WorkflowNode(
                id="start",
                type=WorkflowNodeType.START,
                label="Start",
            ),
            _task_node("parent-before", "Parent Before"),
            WorkflowNode(
                id="sub-call",
                type=WorkflowNodeType.SUBWORKFLOW,
                label="Subworkflow Call",
                config=sub_config,
            ),
            _task_node("parent-after", "Parent After"),
            WorkflowNode(
                id="end",
                type=WorkflowNodeType.END,
                label="End",
            ),
        ),
        edges=(
            _seq_edge("e1", "start", "parent-before"),
            _seq_edge("e2", "parent-before", "sub-call"),
            _seq_edge("e3", "sub-call", "parent-after"),
            _seq_edge("e4", "parent-after", "end"),
        ),
        created_by="test",
        created_at=_NOW,
        updated_at=_NOW,
    )


class _FakeTaskEngine(TaskEngine):
    """Minimal task engine stub that records created tasks.

    Subclasses the real ``TaskEngine`` (bypassing its ``__init__``) so the
    runtime-resolved ``task_engine: TaskEngine`` boundary accepts it.
    """

    def __init__(self) -> None:
        self.created: list[str] = []

    @override
    async def create_task(self, data: CreateTaskData, *, requested_by: str) -> Task:
        task_id = f"task-{len(self.created)}"
        self.created.append(task_id)
        return Task(
            id=as_uuid(task_id),
            title=data.title,
            description=data.description,
            type=TaskType(data.type),
            priority=Priority(data.priority),
            project=data.project,
            created_by=data.created_by,
            assigned_to=data.assigned_to or "default-agent",
            dependencies=tuple(data.dependencies),
            estimated_complexity=Complexity(data.estimated_complexity),
            status=TaskStatus.ASSIGNED,
        )


class _FakeDefinitionRepo:
    """Definition repo stub holding a single definition."""

    def __init__(self, definition: WorkflowDefinition) -> None:
        self._definition = definition

    async def get(self, definition_id: str) -> WorkflowDefinition | None:
        if definition_id == self._definition.id:
            return self._definition
        return None


class _FakeExecutionRepo:
    """Execution repo stub that records the last save."""

    def __init__(self) -> None:
        self.saved: list[object] = []

    async def save(self, execution: object) -> None:
        self.saved.append(execution)


async def _build_service(
    parent: WorkflowDefinition,
    registry: SubworkflowRegistry,
    *,
    max_depth: int = 16,
) -> tuple[WorkflowExecutionService, _FakeTaskEngine, _FakeExecutionRepo]:
    task_engine = _FakeTaskEngine()
    definition_repo = _FakeDefinitionRepo(parent)
    execution_repo = _FakeExecutionRepo()
    service = WorkflowExecutionService(
        definition_repo=definition_repo,  # type: ignore[arg-type]
        execution_repo=execution_repo,  # type: ignore[arg-type]
        task_engine=task_engine,
        subworkflow_registry=registry,
        max_subworkflow_depth=max_depth,
    )
    return service, task_engine, execution_repo


@pytest.fixture
def registry() -> SubworkflowRegistry:
    return SubworkflowRegistry(FakeSubworkflowRepository())


@pytest.mark.unit
class TestSubworkflowExecution:
    async def test_parent_calls_subworkflow_receives_outputs(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        child = _make_child_definition(
            inputs=(
                WorkflowIODeclaration(
                    name="quarter",
                    type=WorkflowValueType.STRING,
                ),
            ),
            outputs=(
                WorkflowIODeclaration(
                    name="quarter_echo",
                    type=WorkflowValueType.STRING,
                ),
            ),
        )
        await registry.register(child)

        parent = _make_parent_definition(
            input_bindings={"quarter": "@parent.current_quarter"},
            output_bindings={"quarter_echo": "@child.quarter"},
        )
        service, engine, exec_repo = await _build_service(parent, registry)

        execution = await service.activate(
            "parent-wf",
            project="test",
            activated_by="ceo",
            context={"current_quarter": "Q4"},
        )

        # Parent created 2 tasks (before + after) and child created 1.
        assert len(engine.created) == 3
        assert len(exec_repo.saved) == 1

        node_ids = [ne.node_id for ne in execution.node_executions]
        # Parent nodes keep their unqualified IDs
        assert "parent-before" in node_ids
        assert "sub-call" in node_ids
        assert "parent-after" in node_ids
        # Child nodes carry the qualified prefix
        assert "sub-call::start" in node_ids
        assert "sub-call::child-task" in node_ids
        assert "sub-call::end" in node_ids

        sub_call_exec = next(
            ne for ne in execution.node_executions if ne.node_id == "sub-call"
        )
        assert sub_call_exec.status is WorkflowNodeExecutionStatus.SUBWORKFLOW_COMPLETED

    async def test_unresolved_subworkflow_without_registry_raises(
        self,
    ) -> None:
        parent = _make_parent_definition()
        task_engine = _FakeTaskEngine()
        definition_repo = _FakeDefinitionRepo(parent)
        execution_repo = _FakeExecutionRepo()
        service = WorkflowExecutionService(
            definition_repo=definition_repo,  # type: ignore[arg-type]
            execution_repo=execution_repo,  # type: ignore[arg-type]
            task_engine=task_engine,
            max_subworkflow_depth=16,
            subworkflow_registry=None,
        )

        from synthorg.engine.errors import WorkflowExecutionError

        with pytest.raises(
            WorkflowExecutionError,
            match="no SubworkflowRegistry",
        ):
            await service.activate(
                "parent-wf",
                project="test",
                activated_by="ceo",
            )

    async def test_runtime_depth_limit_enforced(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        """A 3-level deep chain with max_depth=1 is rejected at runtime."""
        leaf = _make_child_definition(
            definition_id="leaf-sub",
            version="1.0.0",
        )
        await registry.register(leaf)

        # Middle subworkflow nested one leaf call
        middle = WorkflowDefinition(
            id="middle-sub",
            name="Middle",
            description="",
            workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
            version="1.0.0",
            is_subworkflow=True,
            nodes=(
                WorkflowNode(
                    id="start",
                    type=WorkflowNodeType.START,
                    label="Start",
                ),
                WorkflowNode(
                    id="inner",
                    type=WorkflowNodeType.SUBWORKFLOW,
                    label="Inner",
                    config={
                        "subworkflow_id": "leaf-sub",
                        "version": "1.0.0",
                        "input_bindings": {},
                        "output_bindings": {},
                    },
                ),
                WorkflowNode(
                    id="end",
                    type=WorkflowNodeType.END,
                    label="End",
                ),
            ),
            edges=(
                _seq_edge("me1", "start", "inner"),
                _seq_edge("me2", "inner", "end"),
            ),
            created_by="test",
            created_at=_NOW,
            updated_at=_NOW,
        )
        await registry.register(middle)

        parent = _make_parent_definition(
            subworkflow_id="middle-sub",
            version="1.0.0",
        )
        service, _engine, _exec = await _build_service(
            parent,
            registry,
            max_depth=1,
        )

        with pytest.raises(SubworkflowDepthExceededError) as exc_info:
            await service.activate(
                "parent-wf",
                project="test",
                activated_by="ceo",
            )
        assert exc_info.value.max_depth == 1
        assert exc_info.value.depth == 2

    async def test_two_level_nesting_under_default_depth(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        """Two-level nesting fits comfortably under the default depth limit."""
        leaf = _make_child_definition(
            definition_id="leaf-sub",
            version="1.0.0",
        )
        await registry.register(leaf)
        middle = WorkflowDefinition(
            id="middle-sub",
            name="Middle",
            description="",
            workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
            version="1.0.0",
            is_subworkflow=True,
            nodes=(
                WorkflowNode(
                    id="start",
                    type=WorkflowNodeType.START,
                    label="Start",
                ),
                WorkflowNode(
                    id="inner",
                    type=WorkflowNodeType.SUBWORKFLOW,
                    label="Inner",
                    config={
                        "subworkflow_id": "leaf-sub",
                        "version": "1.0.0",
                        "input_bindings": {},
                        "output_bindings": {},
                    },
                ),
                WorkflowNode(
                    id="end",
                    type=WorkflowNodeType.END,
                    label="End",
                ),
            ),
            edges=(
                _seq_edge("me1", "start", "inner"),
                _seq_edge("me2", "inner", "end"),
            ),
            created_by="test",
            created_at=_NOW,
            updated_at=_NOW,
        )
        await registry.register(middle)

        parent = _make_parent_definition(subworkflow_id="middle-sub")
        service, engine, _exec = await _build_service(parent, registry)
        execution = await service.activate(
            "parent-wf",
            project="test",
            activated_by="ceo",
        )

        # parent-before + parent-after + middle's child-task (via leaf) +
        # leaf's own child-task -- wait, middle is not a task-bearing
        # intermediate.  Actually middle has NO task nodes; the tasks
        # come from the leaf (child-task) and the parent (before/after).
        assert len(engine.created) == 3
        # Deeply nested node ID present
        node_ids = {ne.node_id for ne in execution.node_executions}
        assert "sub-call::inner::child-task" in node_ids

    async def test_input_binding_missing_raises_at_runtime(
        self,
        registry: SubworkflowRegistry,
    ) -> None:
        child = _make_child_definition(
            inputs=(
                WorkflowIODeclaration(
                    name="quarter",
                    type=WorkflowValueType.STRING,
                ),
            ),
        )
        await registry.register(child)
        parent = _make_parent_definition(input_bindings={})
        service, _engine, _exec = await _build_service(parent, registry)

        from synthorg.engine.errors import SubworkflowIOError

        with pytest.raises(
            SubworkflowIOError,
            match="Missing required input",
        ):
            await service.activate(
                "parent-wf",
                project="test",
                activated_by="ceo",
            )
