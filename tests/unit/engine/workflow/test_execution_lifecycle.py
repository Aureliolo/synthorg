"""Tests for workflow execution COMPLETED and FAILED transitions."""

import copy
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import override
from uuid import uuid4

import pytest

from synthorg.core.persistence_errors import (
    DuplicateRecordError,
    PersistenceVersionConflictError,
    RecordNotFoundError,
)
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.errors import (
    WorkflowExecutionError,
    WorkflowExecutionNotFoundError,
)
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import CreateTaskData, TaskStateChanged
from synthorg.engine.workflow.definition import WorkflowDefinition
from synthorg.engine.workflow.enums import (
    WorkflowEdgeType,
    WorkflowExecutionStatus,
    WorkflowNodeExecutionStatus,
)
from synthorg.engine.workflow.execution_models import WorkflowExecution
from synthorg.engine.workflow.execution_service import (
    WorkflowExecutionService,
)
from synthorg.persistence.workflow_execution_protocol import (
    WorkflowExecutionFilterSpec,
)
from tests._shared import as_uuid
from tests.unit.engine.workflow.conftest import (
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
        self._store[str(definition.id)] = copy.deepcopy(definition)

    async def create_if_absent(self, definition: WorkflowDefinition) -> bool:
        if str(definition.id) in self._store:
            return False
        self._store[str(definition.id)] = copy.deepcopy(definition)
        return True

    async def update_if_exists(self, definition: WorkflowDefinition) -> bool:
        if str(definition.id) not in self._store:
            return False
        self._store[str(definition.id)] = copy.deepcopy(definition)
        return True

    async def get(self, definition_id: str) -> WorkflowDefinition | None:
        stored = self._store.get(definition_id)
        return copy.deepcopy(stored) if stored is not None else None

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[WorkflowDefinition, ...]:
        del filter_spec, limit, offset
        return tuple(self._store.values())

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[WorkflowDefinition, ...]:
        del limit, offset
        return tuple(self._store.values())

    async def count(self, filter_spec: object) -> int:
        del filter_spec
        return len(self._store)

    async def delete(self, definition_id: str) -> bool:
        return self._store.pop(definition_id, None) is not None


class FakeExecutionRepo:
    """In-memory fake for WorkflowExecutionRepository."""

    def __init__(self) -> None:
        self._store: dict[str, WorkflowExecution] = {}

    async def save(self, execution: WorkflowExecution) -> None:
        stored = self._store.get(str(execution.id))
        if stored is None:
            if execution.version != 1:
                msg = f"Execution {execution.id!r} not found"
                raise RecordNotFoundError(msg)
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
        self._store[str(execution.id)] = copy.deepcopy(execution)

    async def get(self, execution_id: str) -> WorkflowExecution | None:
        stored = self._store.get(execution_id)
        return copy.deepcopy(stored) if stored is not None else None

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[WorkflowExecution, ...]:
        executions = sorted(
            self._store.values(),
            key=lambda e: e.id,
        )
        return tuple(copy.deepcopy(e) for e in executions[offset : offset + limit])

    async def query(
        self,
        filter_spec: WorkflowExecutionFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[WorkflowExecution, ...]:
        result = list(self._store.values())

        if filter_spec.definition_id is not None:
            result = [e for e in result if e.definition_id == filter_spec.definition_id]

        if filter_spec.status is not None:
            result = [e for e in result if e.status == filter_spec.status]

        result = sorted(
            result,
            key=lambda e: (e.updated_at, e.id),
            reverse=False,
        )
        result.reverse()  # Sort by updated_at DESC then id ASC

        return tuple(copy.deepcopy(e) for e in result[offset : offset + limit])

    async def count(self, filter_spec: WorkflowExecutionFilterSpec) -> int:
        result = list(self._store.values())

        if filter_spec.definition_id is not None:
            result = [e for e in result if e.definition_id == filter_spec.definition_id]

        if filter_spec.status is not None:
            result = [e for e in result if e.status == filter_spec.status]

        return len(result)

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


class FakeTaskEngine(TaskEngine):
    """Minimal fake for TaskEngine.create_task().

    Subclasses the real ``TaskEngine`` (bypassing its ``__init__``) so the
    runtime-resolved ``task_engine: TaskEngine`` boundary accepts it, while
    overriding only ``create_task`` to record calls and return a real Task.
    """

    def __init__(self) -> None:
        self.created_tasks: list[tuple[CreateTaskData, str]] = []

    @override
    async def create_task(
        self,
        data: CreateTaskData,
        *,
        requested_by: str,
    ) -> Task:
        self.created_tasks.append((data, requested_by))
        task_id = f"task-{uuid4().hex[:12]}"
        return Task(
            id=as_uuid(task_id),
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
    return WorkflowExecutionService(
        definition_repo=def_repo,
        execution_repo=exec_repo,
        task_engine=task_engine,
        max_subworkflow_depth=16,
    )


# ── Helpers ───────────────────────────────────────────────────────


def _make_task_event(
    task_id: str,
    new_status: TaskStatus,
    previous_status: TaskStatus = TaskStatus.IN_PROGRESS,
) -> TaskStateChanged:
    """Build a TaskStateChanged for a transition mutation."""
    return TaskStateChanged(
        mutation_type="transition",
        request_id=f"req-{uuid4().hex[:8]}",
        requested_by="test",
        task_id=task_id,
        task=None,
        previous_status=previous_status,
        new_status=new_status,
        version=2,
        reason="test transition",
        timestamp=datetime.now(UTC),
    )


async def _activate_simple(
    service: WorkflowExecutionService,
    def_repo: FakeDefinitionRepo,
) -> WorkflowExecution:
    """Activate a START -> TASK -> END workflow."""
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
    return await service.activate(
        str(wf.id),
        project="proj-1",
        activated_by="test",
    )


async def _activate_parallel(
    service: WorkflowExecutionService,
    def_repo: FakeDefinitionRepo,
) -> WorkflowExecution:
    """Activate a workflow with two parallel TASK nodes."""
    wf = make_workflow(
        nodes=(
            make_start_node(),
            make_split_node("split-1"),
            make_task_node_full("task-a", config={"title": "A"}),
            make_task_node_full("task-b", config={"title": "B"}),
            make_join_node("join-1"),
            make_end_node(),
        ),
        edges=(
            make_edge("e1", "start-1", "split-1"),
            make_edge(
                "e2",
                "split-1",
                "task-a",
                WorkflowEdgeType.PARALLEL_BRANCH,
            ),
            make_edge(
                "e3",
                "split-1",
                "task-b",
                WorkflowEdgeType.PARALLEL_BRANCH,
            ),
            make_edge("e4", "task-a", "join-1"),
            make_edge("e5", "task-b", "join-1"),
            make_edge("e6", "join-1", "end-1"),
        ),
    )
    await def_repo.save(wf)
    return await service.activate(
        str(wf.id),
        project="proj-1",
        activated_by="test",
    )


def _get_task_ids(execution: WorkflowExecution) -> dict[str, str]:
    """Map node_id -> task_id for TASK nodes."""
    return {
        ne.node_id: ne.task_id
        for ne in execution.node_executions
        if ne.task_id is not None
    }


# ── complete_execution tests ──────────────────────────────────────


class TestCompleteExecution:
    """Tests for complete_execution()."""

    @pytest.mark.unit
    async def test_complete_running_execution(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
        exec_repo: FakeExecutionRepo,
    ) -> None:
        exe = await _activate_simple(service, def_repo)
        assert exe.status is WorkflowExecutionStatus.RUNNING

        result = await service.complete_execution(str(exe.id))
        assert result.status is WorkflowExecutionStatus.COMPLETED
        assert result.completed_at is not None
        assert result.error is None
        assert result.version == exe.version + 1

        # Persisted
        stored = await exec_repo.get(str(exe.id))
        assert stored is not None
        assert stored.status is WorkflowExecutionStatus.COMPLETED

    @pytest.mark.unit
    async def test_complete_not_found_raises(
        self,
        service: WorkflowExecutionService,
    ) -> None:
        with pytest.raises(WorkflowExecutionNotFoundError):
            await service.complete_execution("nonexistent")

    @pytest.mark.unit
    async def test_complete_already_terminal_raises(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
    ) -> None:
        exe = await _activate_simple(service, def_repo)
        await service.complete_execution(str(exe.id))

        with pytest.raises(WorkflowExecutionError, match="expected 'running'"):
            await service.complete_execution(str(exe.id))

    @pytest.mark.unit
    async def test_complete_cancelled_raises(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
    ) -> None:
        exe = await _activate_simple(service, def_repo)
        await service.cancel_execution(str(exe.id), cancelled_by="test")

        with pytest.raises(WorkflowExecutionError, match="expected 'running'"):
            await service.complete_execution(str(exe.id))


# ── fail_execution tests ──────────────────────────────────────────


class TestFailExecution:
    """Tests for fail_execution()."""

    @pytest.mark.unit
    async def test_fail_running_execution(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
        exec_repo: FakeExecutionRepo,
    ) -> None:
        exe = await _activate_simple(service, def_repo)

        result = await service.fail_execution(
            str(exe.id),
            error="Task blew up",
        )
        assert result.status is WorkflowExecutionStatus.FAILED
        assert result.completed_at is not None
        assert result.error == "Task blew up"
        assert result.version == exe.version + 1

        stored = await exec_repo.get(str(exe.id))
        assert stored is not None
        assert stored.status is WorkflowExecutionStatus.FAILED

    @pytest.mark.unit
    async def test_fail_not_found_raises(
        self,
        service: WorkflowExecutionService,
    ) -> None:
        with pytest.raises(WorkflowExecutionNotFoundError):
            await service.fail_execution(
                "nonexistent",
                error="boom",
            )

    @pytest.mark.unit
    async def test_fail_already_terminal_raises(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
    ) -> None:
        exe = await _activate_simple(service, def_repo)
        await service.cancel_execution(str(exe.id), cancelled_by="test")

        with pytest.raises(WorkflowExecutionError, match="expected 'running'"):
            await service.fail_execution(str(exe.id), error="late failure")


# ── handle_task_state_changed tests ───────────────────────────────


class TestHandleTaskStateChanged:
    """Tests for handle_task_state_changed()."""

    @pytest.mark.unit
    async def test_task_completed_updates_node_execution_stays_running(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
        exec_repo: FakeExecutionRepo,
    ) -> None:
        """Two-task workflow: completing one keeps execution RUNNING."""
        exe = await _activate_parallel(service, def_repo)
        task_ids = _get_task_ids(exe)

        event = _make_task_event(
            task_ids["task-a"],
            TaskStatus.COMPLETED,
        )
        await service.handle_task_state_changed(event)

        stored = await exec_repo.get(str(exe.id))
        assert stored is not None
        assert stored.status is WorkflowExecutionStatus.RUNNING
        nmap = {ne.node_id: ne for ne in stored.node_executions}
        assert nmap["task-a"].status is WorkflowNodeExecutionStatus.TASK_COMPLETED
        assert nmap["task-b"].status is WorkflowNodeExecutionStatus.TASK_CREATED

    @pytest.mark.unit
    async def test_all_tasks_completed_transitions_to_completed(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
        exec_repo: FakeExecutionRepo,
    ) -> None:
        exe = await _activate_parallel(service, def_repo)
        task_ids = _get_task_ids(exe)

        await service.handle_task_state_changed(
            _make_task_event(task_ids["task-a"], TaskStatus.COMPLETED),
        )
        await service.handle_task_state_changed(
            _make_task_event(task_ids["task-b"], TaskStatus.COMPLETED),
        )

        stored = await exec_repo.get(str(exe.id))
        assert stored is not None
        assert stored.status is WorkflowExecutionStatus.COMPLETED
        assert stored.completed_at is not None

    @pytest.mark.unit
    async def test_single_task_completed_transitions_to_completed(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
        exec_repo: FakeExecutionRepo,
    ) -> None:
        exe = await _activate_simple(service, def_repo)
        task_ids = _get_task_ids(exe)

        await service.handle_task_state_changed(
            _make_task_event(task_ids["task-1"], TaskStatus.COMPLETED),
        )

        stored = await exec_repo.get(str(exe.id))
        assert stored is not None
        assert stored.status is WorkflowExecutionStatus.COMPLETED

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("terminal_status", "expected_word"),
        [
            (TaskStatus.FAILED, "failed"),
            (TaskStatus.CANCELLED, "cancelled"),
        ],
    )
    async def test_terminal_task_transitions_to_failed(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
        exec_repo: FakeExecutionRepo,
        terminal_status: TaskStatus,
        expected_word: str,
    ) -> None:
        exe = await _activate_simple(service, def_repo)
        task_ids = _get_task_ids(exe)

        await service.handle_task_state_changed(
            _make_task_event(task_ids["task-1"], terminal_status),
        )

        stored = await exec_repo.get(str(exe.id))
        assert stored is not None
        assert stored.status is WorkflowExecutionStatus.FAILED
        assert expected_word in (stored.error or "").lower()

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "terminal_status",
        [TaskStatus.FAILED, TaskStatus.CANCELLED],
    )
    async def test_terminal_task_updates_node_to_task_failed(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
        exec_repo: FakeExecutionRepo,
        terminal_status: TaskStatus,
    ) -> None:
        exe = await _activate_parallel(service, def_repo)
        task_ids = _get_task_ids(exe)

        await service.handle_task_state_changed(
            _make_task_event(task_ids["task-a"], terminal_status),
        )

        stored = await exec_repo.get(str(exe.id))
        assert stored is not None
        nmap = {ne.node_id: ne for ne in stored.node_executions}
        assert nmap["task-a"].status is WorkflowNodeExecutionStatus.TASK_FAILED

    @pytest.mark.unit
    async def test_unrelated_task_ignored(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
        exec_repo: FakeExecutionRepo,
    ) -> None:
        exe = await _activate_simple(service, def_repo)

        await service.handle_task_state_changed(
            _make_task_event("unrelated-task-999", TaskStatus.COMPLETED),
        )

        stored = await exec_repo.get(str(exe.id))
        assert stored is not None
        assert stored.status is WorkflowExecutionStatus.RUNNING

    @pytest.mark.unit
    async def test_completed_execution_ignores_further_events(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
        exec_repo: FakeExecutionRepo,
    ) -> None:
        exe = await _activate_simple(service, def_repo)
        task_ids = _get_task_ids(exe)

        await service.handle_task_state_changed(
            _make_task_event(task_ids["task-1"], TaskStatus.COMPLETED),
        )

        stored = await exec_repo.get(str(exe.id))
        assert stored is not None
        assert stored.status is WorkflowExecutionStatus.COMPLETED

        # Send another event -- should be silently ignored
        await service.handle_task_state_changed(
            _make_task_event(task_ids["task-1"], TaskStatus.COMPLETED),
        )

    @pytest.mark.unit
    async def test_in_progress_status_silently_ignored(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
        exec_repo: FakeExecutionRepo,
    ) -> None:
        """IN_PROGRESS task status is not terminal so the event is ignored."""
        exe = await _activate_simple(service, def_repo)
        task_ids = _get_task_ids(exe)

        event = _make_task_event(
            task_ids["task-1"],
            TaskStatus.IN_PROGRESS,
            previous_status=TaskStatus.ASSIGNED,
        )
        await service.handle_task_state_changed(event)

        stored = await exec_repo.get(str(exe.id))
        assert stored is not None
        assert stored.status is WorkflowExecutionStatus.RUNNING
        # Node status unchanged -- still TASK_CREATED
        nmap = {ne.node_id: ne for ne in stored.node_executions}
        assert nmap["task-1"].status is WorkflowNodeExecutionStatus.TASK_CREATED

    @pytest.mark.unit
    async def test_assigned_status_silently_ignored(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
        exec_repo: FakeExecutionRepo,
    ) -> None:
        """ASSIGNED task status is not terminal so the event is ignored."""
        exe = await _activate_simple(service, def_repo)
        task_ids = _get_task_ids(exe)

        event = _make_task_event(
            task_ids["task-1"],
            TaskStatus.ASSIGNED,
            previous_status=TaskStatus.CREATED,
        )
        await service.handle_task_state_changed(event)

        stored = await exec_repo.get(str(exe.id))
        assert stored is not None
        assert stored.status is WorkflowExecutionStatus.RUNNING
        nmap = {ne.node_id: ne for ne in stored.node_executions}
        assert nmap["task-1"].status is WorkflowNodeExecutionStatus.TASK_CREATED

    @pytest.mark.unit
    async def test_non_transition_mutation_ignored(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
    ) -> None:
        await _activate_simple(service, def_repo)
        event = TaskStateChanged(
            mutation_type="update",
            request_id="req-1",
            requested_by="test",
            task_id="any",
            task=None,
            previous_status=None,
            new_status=None,
            version=1,
            reason=None,
            timestamp=datetime.now(UTC),
        )
        # Should return without error
        await service.handle_task_state_changed(event)

    @pytest.mark.unit
    async def test_skipped_nodes_not_counted_for_completion(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
        exec_repo: FakeExecutionRepo,
    ) -> None:
        """Conditional branch: only the taken branch's tasks matter."""
        from tests.unit.engine.workflow.conftest import (
            make_conditional_node,
        )

        wf = make_workflow(
            nodes=(
                make_start_node(),
                make_conditional_node("cond-1", "true"),
                make_task_node_full(
                    "task-true",
                    config={"title": "True branch"},
                ),
                make_task_node_full(
                    "task-false",
                    config={"title": "False branch"},
                ),
                make_end_node(),
            ),
            edges=(
                make_edge("e1", "start-1", "cond-1"),
                make_edge(
                    "e2",
                    "cond-1",
                    "task-true",
                    WorkflowEdgeType.CONDITIONAL_TRUE,
                ),
                make_edge(
                    "e3",
                    "cond-1",
                    "task-false",
                    WorkflowEdgeType.CONDITIONAL_FALSE,
                ),
                make_edge("e4", "task-true", "end-1"),
                make_edge("e5", "task-false", "end-1"),
            ),
        )
        await def_repo.save(wf)
        exe = await service.activate(
            str(wf.id),
            project="proj-1",
            activated_by="test",
        )

        # task-false should be SKIPPED (condition is "true")
        nmap = {ne.node_id: ne for ne in exe.node_executions}
        assert nmap["task-false"].status is WorkflowNodeExecutionStatus.SKIPPED
        assert nmap["task-true"].status is WorkflowNodeExecutionStatus.TASK_CREATED

        task_ids = _get_task_ids(exe)
        await service.handle_task_state_changed(
            _make_task_event(task_ids["task-true"], TaskStatus.COMPLETED),
        )

        stored = await exec_repo.get(str(exe.id))
        assert stored is not None
        assert stored.status is WorkflowExecutionStatus.COMPLETED


# ── record_workflow_execution + state-transition log wiring ────────


class TestWorkflowMetricsAndLogs:
    """Verify lifecycle terminal handlers emit metrics + transition logs."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("terminal_op", "expected_status", "extra_log_kwargs"),
        [
            pytest.param(
                lambda svc, exe: svc.complete_execution(str(exe.id)),
                "completed",
                {},
                id="complete",
            ),
            pytest.param(
                lambda svc, exe: svc.fail_execution(str(exe.id), error="boom"),
                "failed",
                {"error": "boom"},
                id="fail",
            ),
            pytest.param(
                lambda svc, exe: svc.cancel_execution(
                    str(exe.id), cancelled_by="alice"
                ),
                "cancelled",
                {},
                id="cancel",
            ),
        ],
    )
    async def test_terminal_emits_metric_and_status_transitioned_log(
        self,
        service: WorkflowExecutionService,
        def_repo: FakeDefinitionRepo,
        terminal_op: Callable[
            [WorkflowExecutionService, WorkflowExecution],
            Awaitable[WorkflowExecution],
        ],
        expected_status: str,
        extra_log_kwargs: dict[str, str],
    ) -> None:
        """All three terminal handlers share the same observability contract.

        Asserts: ``record_workflow_execution`` is called exactly once
        with the right ``workflow_definition_id`` / ``status`` /
        non-negative ``duration_seconds``, AND
        ``WORKFLOW_EXEC_STATUS_TRANSITIONED`` lands with
        ``from_status="running"`` plus the expected ``to_status`` and
        any handler-specific kwargs (e.g. ``error="boom"`` on the
        failure path).
        """
        from unittest.mock import patch

        import structlog.testing

        from synthorg.observability.events.workflow_execution import (
            WORKFLOW_EXEC_STATUS_TRANSITIONED,
        )

        exe = await _activate_simple(service, def_repo)
        with (
            structlog.testing.capture_logs() as logs,
            patch(
                "synthorg.engine.workflow.execution_lifecycle"
                ".record_workflow_execution",
            ) as mock_metric,
        ):
            await terminal_op(service, exe)

        mock_metric.assert_called_once()
        kwargs = mock_metric.call_args.kwargs
        assert kwargs["workflow_definition_id"] == exe.definition_id
        assert kwargs["status"] == expected_status
        assert kwargs["duration_seconds"] >= 0.0

        assert any(
            rec.get("event") == WORKFLOW_EXEC_STATUS_TRANSITIONED
            and rec.get("from_status") == "running"
            and rec.get("to_status") == expected_status
            and all(rec.get(k) == v for k, v in extra_log_kwargs.items())
            for rec in logs
        )
