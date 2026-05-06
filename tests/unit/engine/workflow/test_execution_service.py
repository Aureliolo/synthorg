"""Tests for WorkflowExecutionService."""

import copy
from typing import Any
from uuid import uuid4

import pytest

from synthorg.core.enums import (
    WorkflowEdgeType,
    WorkflowExecutionStatus,
    WorkflowNodeExecutionStatus,
)
from synthorg.core.persistence_errors import (
    DuplicateRecordError,
    PersistenceVersionConflictError,
)
from synthorg.core.task import Task
from synthorg.engine.errors import (
    WorkflowDefinitionInvalidError,
    WorkflowExecutionError,
    WorkflowExecutionNotFoundError,
)
from synthorg.engine.task_engine_models import CreateTaskData
from synthorg.engine.workflow.definition import WorkflowDefinition
from synthorg.engine.workflow.execution_models import (
    WorkflowExecution,
    WorkflowNodeExecution,
)
from synthorg.engine.workflow.execution_service import WorkflowExecutionService
from tests.unit.engine.workflow.conftest import (
    make_assignment_node,
    make_conditional_node,
    make_edge,
    make_end_node,
    make_join_node,
    make_split_node,
    make_start_node,
    make_task_node_full,
    make_workflow,
)

# ── Fakes ─────────────────────────────────────────────────────────


class FakeDefinitionRepo:
    """In-memory fake for WorkflowDefinitionRepository."""

    def __init__(self) -> None:
        self._store: dict[str, WorkflowDefinition] = {}

    async def save(self, definition: WorkflowDefinition) -> None:
        self._store[definition.id] = copy.deepcopy(definition)

    async def create_if_absent(self, definition: WorkflowDefinition) -> bool:
        if definition.id in self._store:
            return False
        self._store[definition.id] = copy.deepcopy(definition)
        return True

    async def update_if_exists(self, definition: WorkflowDefinition) -> bool:
        if definition.id not in self._store:
            return False
        self._store[definition.id] = copy.deepcopy(definition)
        return True

    async def get(self, definition_id: str) -> WorkflowDefinition | None:
        stored = self._store.get(definition_id)
        return copy.deepcopy(stored) if stored is not None else None

    async def list_definitions(
        self,
        *,
        workflow_type: object = None,
    ) -> tuple[WorkflowDefinition, ...]:
        return tuple(self._store.values())

    async def delete(self, definition_id: str) -> bool:
        return self._store.pop(definition_id, None) is not None


class FakeExecutionRepo:
    """In-memory fake for WorkflowExecutionRepository."""

    def __init__(self) -> None:
        self._store: dict[str, WorkflowExecution] = {}

    async def save(self, execution: WorkflowExecution) -> None:
        stored = self._store.get(execution.id)
        if stored is None:
            if execution.version != 1:
                msg = f"Cannot insert with version {execution.version}"
                raise PersistenceVersionConflictError(msg)
        else:
            if execution.version == 1:
                msg = f"Execution {execution.id!r} already exists"
                raise DuplicateRecordError(msg)
            if execution.version != stored.version + 1:
                msg = (
                    f"Version conflict: expected {stored.version + 1},"
                    f" got {execution.version}"
                )
                raise PersistenceVersionConflictError(msg)
        self._store[execution.id] = copy.deepcopy(execution)

    async def get(self, execution_id: str) -> WorkflowExecution | None:
        stored = self._store.get(execution_id)
        return copy.deepcopy(stored) if stored is not None else None

    async def list_by_definition(
        self,
        definition_id: str,
    ) -> tuple[WorkflowExecution, ...]:
        return tuple(
            copy.deepcopy(e)
            for e in self._store.values()
            if e.definition_id == definition_id
        )

    async def list_by_status(
        self,
        status: object,
    ) -> tuple[WorkflowExecution, ...]:
        return tuple(e for e in self._store.values() if e.status == status)

    async def find_by_task_id(
        self,
        task_id: str,
    ) -> WorkflowExecution | None:
        for execution in self._store.values():
            if execution.status != WorkflowExecutionStatus.RUNNING:
                continue
            for ne in execution.node_executions:
                if ne.task_id == task_id:
                    return copy.deepcopy(execution)
        return None

    async def delete(self, execution_id: str) -> bool:
        return self._store.pop(execution_id, None) is not None


class FakeTaskEngine:
    """Minimal fake for TaskEngine.create_task()."""

    def __init__(self) -> None:
        self.created_tasks: list[tuple[CreateTaskData, str]] = []

    async def create_task(
        self,
        data: CreateTaskData,
        *,
        requested_by: str,
    ) -> Task:
        self.created_tasks.append((data, requested_by))
        task_id = f"task-{uuid4().hex[:12]}"
        return Task(
            id=task_id,
            title=data.title,
            description=data.description,
            type=data.type,
            priority=data.priority,
            project=data.project,
            created_by=data.created_by,
            dependencies=data.dependencies,
            estimated_complexity=data.estimated_complexity,
            budget_limit=data.budget_limit,
        )


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def def_repo() -> FakeDefinitionRepo:
    return FakeDefinitionRepo()


@pytest.fixture
def exec_repo() -> FakeExecutionRepo:
    return FakeExecutionRepo()


@pytest.fixture
def task_engine() -> FakeTaskEngine:
    return FakeTaskEngine()


@pytest.fixture
def service(
    def_repo: FakeDefinitionRepo,
    exec_repo: FakeExecutionRepo,
    task_engine: FakeTaskEngine,
) -> WorkflowExecutionService:
    engine: Any = task_engine
    return WorkflowExecutionService(
        definition_repo=def_repo,
        execution_repo=exec_repo,
        task_engine=engine,
        max_subworkflow_depth=16,
    )


# ── Helper ────────────────────────────────────────────────────────


def _node_map(
    execution: WorkflowExecution,
) -> dict[str, WorkflowNodeExecution]:
    """Build a node_id -> WorkflowNodeExecution map."""
    return {ne.node_id: ne for ne in execution.node_executions}


# ── Tests ─────────────────────────────────────────────────────────


class TestActivateSimple:
    """Activate a simple START -> TASK -> END workflow."""

    @pytest.mark.unit
    async def test_creates_one_task(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
        task_engine: FakeTaskEngine,
    ) -> None:
        wf = make_workflow(
            nodes=(
                make_start_node(),
                make_task_node_full("task-1", config={"title": "Build auth"}),
                make_end_node(),
            ),
            edges=(
                make_edge("e1", "start-1", "task-1"),
                make_edge("e2", "task-1", "end-1"),
            ),
        )
        await def_repo.save(wf)
        exe = await service.activate(
            wf.id,
            project="test-proj",
            activated_by="user-1",
        )

        assert exe.status is WorkflowExecutionStatus.RUNNING
        assert exe.definition_id == wf.id
        assert exe.project == "test-proj"
        assert len(task_engine.created_tasks) == 1

        data, req_by = task_engine.created_tasks[0]
        assert data.title == "Build auth"
        assert data.project == "test-proj"
        assert req_by == "workflow-engine"

    @pytest.mark.unit
    async def test_node_executions_tracked(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
    ) -> None:
        wf = make_workflow(
            nodes=(
                make_start_node(),
                make_task_node_full("task-1", config={"title": "Work"}),
                make_end_node(),
            ),
            edges=(
                make_edge("e1", "start-1", "task-1"),
                make_edge("e2", "task-1", "end-1"),
            ),
        )
        await def_repo.save(wf)
        exe = await service.activate(
            wf.id,
            project="proj",
            activated_by="user",
        )

        nmap = _node_map(exe)
        assert nmap["start-1"].status is WorkflowNodeExecutionStatus.COMPLETED
        assert nmap["task-1"].status is WorkflowNodeExecutionStatus.TASK_CREATED
        assert nmap["task-1"].task_id is not None
        assert nmap["end-1"].status is WorkflowNodeExecutionStatus.COMPLETED


class TestActivateSequential:
    """Sequential: START -> TASK_A -> TASK_B -> END."""

    @pytest.mark.unit
    async def test_sequential_dependencies(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
        task_engine: FakeTaskEngine,
    ) -> None:
        wf = make_workflow(
            nodes=(
                make_start_node(),
                make_task_node_full("task-a", config={"title": "Step A"}),
                make_task_node_full("task-b", config={"title": "Step B"}),
                make_end_node(),
            ),
            edges=(
                make_edge("e1", "start-1", "task-a"),
                make_edge("e2", "task-a", "task-b"),
                make_edge("e3", "task-b", "end-1"),
            ),
        )
        await def_repo.save(wf)
        exe = await service.activate(
            wf.id,
            project="proj",
            activated_by="user",
        )

        assert len(task_engine.created_tasks) == 2
        nmap = _node_map(exe)
        task_a_id = nmap["task-a"].task_id
        assert task_a_id is not None

        # Second task should depend on the first via CreateTaskData.dependencies
        data_b, _ = task_engine.created_tasks[1]
        assert data_b.title == "Step B"
        assert task_a_id in data_b.dependencies


class TestActivateParallel:
    """Parallel: START -> SPLIT -> [TASK_A, TASK_B] -> JOIN -> TASK_C -> END."""

    @pytest.mark.unit
    async def test_parallel_creates_three_tasks(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
        task_engine: FakeTaskEngine,
    ) -> None:
        wf = make_workflow(
            nodes=(
                make_start_node(),
                make_split_node("split-1"),
                make_task_node_full("task-a", config={"title": "Branch A"}),
                make_task_node_full("task-b", config={"title": "Branch B"}),
                make_join_node("join-1"),
                make_task_node_full("task-c", config={"title": "After merge"}),
                make_end_node(),
            ),
            edges=(
                make_edge("e1", "start-1", "split-1"),
                make_edge(
                    "e2",
                    "split-1",
                    "task-a",
                    edge_type=WorkflowEdgeType.PARALLEL_BRANCH,
                ),
                make_edge(
                    "e3",
                    "split-1",
                    "task-b",
                    edge_type=WorkflowEdgeType.PARALLEL_BRANCH,
                ),
                make_edge("e4", "task-a", "join-1"),
                make_edge("e5", "task-b", "join-1"),
                make_edge("e6", "join-1", "task-c"),
                make_edge("e7", "task-c", "end-1"),
            ),
        )
        await def_repo.save(wf)
        exe = await service.activate(
            wf.id,
            project="proj",
            activated_by="user",
        )

        assert len(task_engine.created_tasks) == 3
        nmap = _node_map(exe)
        assert nmap["split-1"].status is WorkflowNodeExecutionStatus.COMPLETED
        assert nmap["join-1"].status is WorkflowNodeExecutionStatus.COMPLETED
        assert nmap["task-a"].status is WorkflowNodeExecutionStatus.TASK_CREATED
        assert nmap["task-b"].status is WorkflowNodeExecutionStatus.TASK_CREATED
        assert nmap["task-c"].status is WorkflowNodeExecutionStatus.TASK_CREATED

        # task-c should depend on both parallel branches
        task_a_id = nmap["task-a"].task_id
        task_b_id = nmap["task-b"].task_id
        data_c, _ = task_engine.created_tasks[2]
        assert task_a_id in data_c.dependencies
        assert task_b_id in data_c.dependencies


class TestActivateConditional:
    """Conditional branching."""

    @pytest.mark.unit
    async def test_true_branch_taken(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
        task_engine: FakeTaskEngine,
    ) -> None:
        wf = make_workflow(
            nodes=(
                make_start_node(),
                make_conditional_node("cond-1", condition_expression="enabled"),
                make_task_node_full("task-true", config={"title": "True path"}),
                make_task_node_full("task-false", config={"title": "False path"}),
                make_end_node(),
            ),
            edges=(
                make_edge("e1", "start-1", "cond-1"),
                make_edge(
                    "e2",
                    "cond-1",
                    "task-true",
                    edge_type=WorkflowEdgeType.CONDITIONAL_TRUE,
                ),
                make_edge(
                    "e3",
                    "cond-1",
                    "task-false",
                    edge_type=WorkflowEdgeType.CONDITIONAL_FALSE,
                ),
                make_edge("e4", "task-true", "end-1"),
                make_edge("e5", "task-false", "end-1"),
            ),
        )
        await def_repo.save(wf)
        exe = await service.activate(
            wf.id,
            project="proj",
            activated_by="user",
            context={"enabled": True},
        )

        nmap = _node_map(exe)
        assert nmap["task-true"].status is WorkflowNodeExecutionStatus.TASK_CREATED
        assert nmap["task-false"].status is WorkflowNodeExecutionStatus.SKIPPED
        assert len(task_engine.created_tasks) == 1
        assert task_engine.created_tasks[0][0].title == "True path"

    @pytest.mark.unit
    async def test_false_branch_taken(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
        task_engine: FakeTaskEngine,
    ) -> None:
        wf = make_workflow(
            nodes=(
                make_start_node(),
                make_conditional_node("cond-1", condition_expression="enabled"),
                make_task_node_full("task-true", config={"title": "True path"}),
                make_task_node_full("task-false", config={"title": "False path"}),
                make_end_node(),
            ),
            edges=(
                make_edge("e1", "start-1", "cond-1"),
                make_edge(
                    "e2",
                    "cond-1",
                    "task-true",
                    edge_type=WorkflowEdgeType.CONDITIONAL_TRUE,
                ),
                make_edge(
                    "e3",
                    "cond-1",
                    "task-false",
                    edge_type=WorkflowEdgeType.CONDITIONAL_FALSE,
                ),
                make_edge("e4", "task-true", "end-1"),
                make_edge("e5", "task-false", "end-1"),
            ),
        )
        await def_repo.save(wf)
        exe = await service.activate(
            wf.id,
            project="proj",
            activated_by="user",
            context={"enabled": False},
        )

        nmap = _node_map(exe)
        assert nmap["task-true"].status is WorkflowNodeExecutionStatus.SKIPPED
        assert nmap["task-false"].status is WorkflowNodeExecutionStatus.TASK_CREATED
        assert len(task_engine.created_tasks) == 1
        assert task_engine.created_tasks[0][0].title == "False path"


class TestActivateAgentAssignment:
    """Agent assignment metadata propagation."""

    @pytest.mark.unit
    async def test_assignment_applies_to_next_task(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
        task_engine: FakeTaskEngine,
    ) -> None:
        wf = make_workflow(
            nodes=(
                make_start_node(),
                make_assignment_node("assign-1", agent_name="agent-x"),
                make_task_node_full("task-1", config={"title": "Assigned work"}),
                make_end_node(),
            ),
            edges=(
                make_edge("e1", "start-1", "assign-1"),
                make_edge("e2", "assign-1", "task-1"),
                make_edge("e3", "task-1", "end-1"),
            ),
        )
        await def_repo.save(wf)
        exe = await service.activate(
            wf.id,
            project="proj",
            activated_by="user",
        )

        nmap = _node_map(exe)
        assert nmap["assign-1"].status is WorkflowNodeExecutionStatus.COMPLETED
        assert nmap["task-1"].status is WorkflowNodeExecutionStatus.TASK_CREATED
        # Check that assigned_to was passed
        data, _ = task_engine.created_tasks[0]
        assert data.assigned_to == "agent-x"


class TestActivateErrors:
    """Error cases."""

    @pytest.mark.unit
    async def test_definition_not_found(
        self,
        service: WorkflowExecutionService,
    ) -> None:
        with pytest.raises(WorkflowExecutionNotFoundError):
            await service.activate(
                "nonexistent",
                project="proj",
                activated_by="user",
            )

    @pytest.mark.unit
    async def test_invalid_definition_rejected(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
    ) -> None:
        """A definition with unreachable nodes should fail validation."""
        wf = make_workflow(
            nodes=(
                make_start_node(),
                make_task_node_full("task-1", config={"title": "Work"}),
                make_task_node_full("task-orphan", config={"title": "Orphan"}),
                make_end_node(),
            ),
            edges=(
                make_edge("e1", "start-1", "task-1"),
                make_edge("e2", "task-1", "end-1"),
                # task-orphan is unreachable -- no edges lead to it
            ),
        )
        await def_repo.save(wf)
        with pytest.raises(WorkflowDefinitionInvalidError):
            await service.activate(
                wf.id,
                project="proj",
                activated_by="user",
            )


class TestGetAndList:
    """Retrieval operations."""

    @pytest.mark.unit
    async def test_get_execution(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
    ) -> None:
        wf = make_workflow(
            nodes=(
                make_start_node(),
                make_task_node_full("task-1", config={"title": "Work"}),
                make_end_node(),
            ),
            edges=(
                make_edge("e1", "start-1", "task-1"),
                make_edge("e2", "task-1", "end-1"),
            ),
        )
        await def_repo.save(wf)
        exe = await service.activate(
            wf.id,
            project="proj",
            activated_by="user",
        )

        loaded = await service.get_execution(exe.id)
        assert loaded is not None
        assert loaded.id == exe.id

    @pytest.mark.unit
    async def test_list_executions(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
    ) -> None:
        wf = make_workflow(
            nodes=(
                make_start_node(),
                make_task_node_full("task-1", config={"title": "Work"}),
                make_end_node(),
            ),
            edges=(
                make_edge("e1", "start-1", "task-1"),
                make_edge("e2", "task-1", "end-1"),
            ),
        )
        await def_repo.save(wf)
        await service.activate(wf.id, project="proj", activated_by="user")
        await service.activate(wf.id, project="proj", activated_by="user")

        results = await service.list_executions(wf.id)
        assert len(results) == 2


class TestCancelExecution:
    """Cancel a running execution."""

    @pytest.mark.unit
    async def test_cancel_running(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
    ) -> None:
        wf = make_workflow(
            nodes=(
                make_start_node(),
                make_task_node_full("task-1", config={"title": "Work"}),
                make_end_node(),
            ),
            edges=(
                make_edge("e1", "start-1", "task-1"),
                make_edge("e2", "task-1", "end-1"),
            ),
        )
        await def_repo.save(wf)
        exe = await service.activate(
            wf.id,
            project="proj",
            activated_by="user",
        )

        cancelled = await service.cancel_execution(
            exe.id,
            cancelled_by="admin",
        )
        assert cancelled.status is WorkflowExecutionStatus.CANCELLED
        assert cancelled.completed_at is not None

    @pytest.mark.unit
    async def test_cancel_not_found(
        self,
        service: WorkflowExecutionService,
    ) -> None:
        with pytest.raises(WorkflowExecutionNotFoundError):
            await service.cancel_execution(
                "nonexistent",
                cancelled_by="admin",
            )

    @pytest.mark.unit
    async def test_cancel_already_cancelled_raises(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
    ) -> None:
        """Cancelling a terminal execution raises WorkflowExecutionError."""
        wf = make_workflow(
            nodes=(
                make_start_node(),
                make_task_node_full("task-1", config={"title": "Work"}),
                make_end_node(),
            ),
            edges=(
                make_edge("e1", "start-1", "task-1"),
                make_edge("e2", "task-1", "end-1"),
            ),
        )
        await def_repo.save(wf)
        exe = await service.activate(
            wf.id,
            project="proj",
            activated_by="user",
        )
        await service.cancel_execution(exe.id, cancelled_by="admin")

        with pytest.raises(WorkflowExecutionError):
            await service.cancel_execution(exe.id, cancelled_by="admin")
