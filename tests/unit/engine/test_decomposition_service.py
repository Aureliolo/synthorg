"""Tests for decomposition service."""

import asyncio
from unittest.mock import MagicMock

import pytest

from synthorg.core.artifact import ArtifactType
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskStructure, TaskType
from synthorg.engine.decomposition import service as service_module
from synthorg.engine.decomposition.classifier import TaskStructureClassifier
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.manual import ManualDecompositionStrategy
from synthorg.engine.decomposition.models import DecompositionPlan, SubtaskDefinition
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.errors import DecompositionCycleError, DecompositionError
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import as_uuid, mock_of, sid

pytestmark = pytest.mark.unit

#: Short enough that a strategy which never answers trips it immediately.
_A_SHORT_CEILING = 0.01

#: Long enough that an ordinary decomposition finishes well inside it.
_A_GENEROUS_CEILING = 60.0


class _NeverAnsweringStrategy:
    """A planner whose provider never returns, which is the case being bound.

    Waits on an event nothing sets rather than sleeping, so the test measures
    the ceiling instead of racing a duration.
    """

    async def decompose(
        self,
        task: Task,
        context: DecompositionContext,
    ) -> DecompositionPlan:
        await asyncio.Event().wait()
        raise AssertionError  # pragma: no cover -- unreachable past the wait

    def get_strategy_name(self) -> str:
        return "never-answering"

    def plans_any_task(self) -> bool:
        return True


def _make_task(
    task_id: str = "task-svc-1",
    *,
    task_structure: TaskStructure | None = None,
) -> Task:
    """Helper to create a minimal task."""
    return Task(
        id=as_uuid(task_id),
        title="Service Test Task",
        description="A task for service testing",
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project="proj-1",
        created_by="creator",
        task_structure=task_structure,
    )


def _make_plan(
    parent_task_id: str = sid("task-svc-1"),
) -> DecompositionPlan:
    """Helper to create a plan with dependencies."""
    return DecompositionPlan(
        parent_task_id=parent_task_id,
        subtasks=(
            SubtaskDefinition(
                id=sid("sub-1"),
                title="Setup",
                description="Initialize environment",
                required_skills=("python",),
                expected_artifacts=("src/setup.py",),
            ),
            SubtaskDefinition(
                id=sid("sub-2"),
                title="Build",
                description="Build the feature",
                dependencies=(sid("sub-1"),),
                required_skills=("python", "sql"),
                expected_artifacts=("src/feature.py",),
            ),
            SubtaskDefinition(
                id=sid("sub-3"),
                title="Test",
                description="Write tests",
                dependencies=(sid("sub-2"),),
                required_skills=("python", "testing"),
                expected_artifacts=("tests/test_feature.py",),
            ),
        ),
    )


class TestDecompositionService:
    """Tests for DecompositionService."""

    async def test_decompose_creates_tasks(self) -> None:
        """Service creates Task objects from subtask definitions."""
        task = _make_task()
        plan = _make_plan()
        strategy = ManualDecompositionStrategy(plan)
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)
        ctx = DecompositionContext()

        result = await service.decompose_task(task, ctx)

        assert len(result.created_tasks) == 3
        for child_task in result.created_tasks:
            assert child_task.parent_task_id == str(task.id)
            assert child_task.status == TaskStatus.CREATED
            assert child_task.assigned_to is None
            assert child_task.project == task.project
            assert child_task.created_by == task.created_by

    async def test_decompose_arms_guard_with_artifacts_and_criteria(self) -> None:
        """Child tasks carry the subtask artifacts + criteria, arming the guard.

        The direct (no-gate) dispatch path must project the subtask-level
        ``expected_artifacts`` + ``acceptance_criteria`` onto the child
        ``Task``, or the fail-loud zero-artifact guard never engages.
        """
        task = _make_task()
        plan = DecompositionPlan(
            parent_task_id=sid("task-svc-1"),
            subtasks=(
                SubtaskDefinition(
                    id=sid("sub-1"),
                    title="Board",
                    description="Render the grid",
                    expected_artifacts=("src/board.py", "tests/board_test.py"),
                    acceptance_criteria=("grid renders",),
                ),
            ),
        )
        service = DecompositionService(
            ManualDecompositionStrategy(plan), TaskStructureClassifier()
        )

        result = await service.decompose_task(task, DecompositionContext())

        child = result.created_tasks[0]
        assert tuple(a.path for a in child.artifacts_expected) == (
            "src/board.py",
            "tests/board_test.py",
        )
        # Type is inferred from the path so the guard has a typed declaration.
        assert child.artifacts_expected[0].type is ArtifactType.CODE
        assert child.artifacts_expected[1].type is ArtifactType.TESTS
        assert tuple(c.description for c in child.acceptance_criteria) == (
            "grid renders",
        )

    async def test_decompose_non_uuid_subtask_id_raises(self) -> None:
        """A plan with a non-UUID subtask id raises a clear domain error.

        The LLM strategy remaps its labels to UUIDs upstream; a custom
        strategy that hands the service a plain label must fail with a
        ``DecompositionError`` rather than an opaque ``ValueError``.
        """
        task = _make_task()
        plan = DecompositionPlan(
            parent_task_id=str(task.id),
            subtasks=(
                SubtaskDefinition(
                    id="plain-label",
                    title="Child",
                    description="Child task",
                    expected_artifacts=("src/child.py",),
                ),
            ),
        )
        strategy = ManualDecompositionStrategy(plan)
        service = DecompositionService(strategy, TaskStructureClassifier())

        with pytest.raises(DecompositionError, match="not a valid UUID"):
            await service.decompose_task(task, DecompositionContext())

    async def test_decompose_non_canonical_subtask_id_raises(self) -> None:
        """A parseable but non-canonical UUID subtask id is rejected.

        The plan keeps the original id string while the child Task
        canonicalises via ``UUID``; an uppercase (non-canonical) input
        would yield two textual ids for one subtask, so the service must
        reject it rather than silently diverge.
        """
        task = _make_task()
        non_canonical = str(as_uuid("sub-upper")).upper()
        plan = DecompositionPlan(
            parent_task_id=str(task.id),
            subtasks=(
                SubtaskDefinition(
                    id=non_canonical,
                    title="Child",
                    description="Child task",
                    expected_artifacts=("src/child.py",),
                ),
            ),
        )
        strategy = ManualDecompositionStrategy(plan)
        service = DecompositionService(strategy, TaskStructureClassifier())

        with pytest.raises(DecompositionError, match="canonical UUID form"):
            await service.decompose_task(task, DecompositionContext())

    async def test_decompose_builds_edges(self) -> None:
        """Service builds dependency edges from subtask definitions."""
        task = _make_task()
        plan = _make_plan()
        strategy = ManualDecompositionStrategy(plan)
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)
        ctx = DecompositionContext()

        result = await service.decompose_task(task, ctx)

        # sub-1 -> sub-2, sub-2 -> sub-3
        assert (sid("sub-1"), sid("sub-2")) in result.dependency_edges
        assert (sid("sub-2"), sid("sub-3")) in result.dependency_edges
        assert len(result.dependency_edges) == 2

    async def test_decompose_preserves_delegation_chain(self) -> None:
        """Subtasks inherit parent's delegation chain."""
        task = Task(
            id=as_uuid("task-svc-1"),
            title="Delegated Task",
            description="Task with delegation chain",
            type=TaskType.DEVELOPMENT,
            priority=Priority.MEDIUM,
            project="proj-1",
            created_by="creator",
            delegation_chain=("agent-a", "agent-b"),
        )
        plan = DecompositionPlan(
            parent_task_id=str(task.id),
            subtasks=(
                SubtaskDefinition(
                    id=sid("sub-1"),
                    title="Child",
                    description="Child task",
                    expected_artifacts=("src/child.py",),
                ),
            ),
        )
        strategy = ManualDecompositionStrategy(plan)
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)
        ctx = DecompositionContext()

        result = await service.decompose_task(task, ctx)

        assert result.created_tasks[0].delegation_chain == (
            "agent-a",
            "agent-b",
        )

    async def test_undeclared_structure_falls_to_the_classifier(self) -> None:
        """A plan that declared nothing takes the classifier's verdict.

        The classifier reads the task's own explicit declaration first, so
        this is the path by which an operator-set structure reaches the plan.
        """
        task = _make_task(task_structure=TaskStructure.PARALLEL)
        plan = _make_plan()
        assert plan.task_structure is TaskStructure.AUTO
        strategy = ManualDecompositionStrategy(plan)
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)
        ctx = DecompositionContext()

        result = await service.decompose_task(task, ctx)

        assert result.plan.task_structure == TaskStructure.PARALLEL

    async def test_undeclared_structure_falls_to_the_keyword_heuristic(self) -> None:
        """With nothing declared anywhere, the regex heuristic decides."""
        task = Task(
            id=as_uuid("task-svc-1"),
            title="Service Test Task",
            description="A task for service testing",
            type=TaskType.DEVELOPMENT,
            priority=Priority.HIGH,
            project="proj-1",
            created_by="creator",
            dependencies=("dep-1",),
        )
        plan = DecompositionPlan(
            parent_task_id=str(task.id),
            subtasks=(
                SubtaskDefinition(
                    id=sid("sub-1"),
                    title="A",
                    description="Desc A",
                    expected_artifacts=("src/a.py",),
                ),
            ),
        )
        strategy = ManualDecompositionStrategy(plan)
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)
        ctx = DecompositionContext()

        result = await service.decompose_task(task, ctx)

        assert result.plan.task_structure == TaskStructure.SEQUENTIAL

    async def test_declared_structure_survives_contrary_keywords(self) -> None:
        """The planner's declaration is not overruled by a keyword regex.

        The description trips both the sequential ("first") and parallel
        ("in parallel") banks, so the classifier reads it as MIXED while the
        planner, which reasoned over the whole objective, declared PARALLEL.
        """
        task = Task(
            id=as_uuid("task-svc-1"),
            title="Service Test Task",
            description="Do the schema first, then run the checks in parallel",
            type=TaskType.DEVELOPMENT,
            priority=Priority.HIGH,
            project="proj-1",
            created_by="creator",
            dependencies=("dep-1",),
        )
        classifier = TaskStructureClassifier()
        assert classifier.classify(task) == TaskStructure.MIXED

        plan = DecompositionPlan(
            parent_task_id=str(task.id),
            subtasks=(
                SubtaskDefinition(
                    id=sid("sub-1"),
                    title="A",
                    description="Desc A",
                    expected_artifacts=("src/a.py",),
                ),
            ),
            task_structure=TaskStructure.PARALLEL,
        )
        strategy = ManualDecompositionStrategy(plan)
        service = DecompositionService(strategy, classifier)
        ctx = DecompositionContext()

        result = await service.decompose_task(task, ctx)

        assert result.plan.task_structure == TaskStructure.PARALLEL

    async def test_decompose_dag_cycle_raises(self) -> None:
        """Service raises DecompositionCycleError for cyclic plans."""
        task = _make_task()
        # Cycle: sub-1 -> sub-2 -> sub-1
        plan = DecompositionPlan(
            parent_task_id=str(task.id),
            subtasks=(
                SubtaskDefinition(
                    id=sid("sub-1"),
                    title="A",
                    description="Desc A",
                    dependencies=(sid("sub-2"),),
                    expected_artifacts=("src/a.py",),
                ),
                SubtaskDefinition(
                    id=sid("sub-2"),
                    title="B",
                    description="Desc B",
                    dependencies=(sid("sub-1"),),
                    expected_artifacts=("src/b.py",),
                ),
            ),
        )
        strategy = ManualDecompositionStrategy(plan)
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)
        ctx = DecompositionContext()

        with pytest.raises(DecompositionCycleError, match="cycle"):
            await service.decompose_task(task, ctx)

    async def test_decompose_uses_subtask_complexity(self) -> None:
        """Child tasks use subtask's estimated_complexity, not parent's."""
        from synthorg.core.task_enums import Complexity

        task = Task(
            id=as_uuid("task-svc-1"),
            title="Epic Task",
            description="Parent task",
            type=TaskType.DEVELOPMENT,
            priority=Priority.HIGH,
            project="proj-1",
            created_by="creator",
            estimated_complexity=Complexity.EPIC,
        )
        plan = DecompositionPlan(
            parent_task_id=str(task.id),
            subtasks=(
                SubtaskDefinition(
                    id=sid("sub-1"),
                    title="Simple Child",
                    description="Simple subtask",
                    estimated_complexity=Complexity.SIMPLE,
                    expected_artifacts=("src/simple.py",),
                ),
            ),
        )
        strategy = ManualDecompositionStrategy(plan)
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)
        ctx = DecompositionContext()

        result = await service.decompose_task(task, ctx)

        assert result.created_tasks[0].estimated_complexity == Complexity.SIMPLE

    async def test_decompose_exception_propagates(self) -> None:
        """Unexpected exceptions are logged and re-raised."""

        class _FailingStrategy:
            async def decompose(
                self, task: Task, context: DecompositionContext
            ) -> DecompositionPlan:
                msg = "strategy boom"
                raise RuntimeError(msg)

            def get_strategy_name(self) -> str:
                return "failing"

            def plans_any_task(self) -> bool:
                return True

        task = _make_task()
        strategy = _FailingStrategy()
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)
        ctx = DecompositionContext()

        with pytest.raises(RuntimeError, match="strategy boom"):
            await service.decompose_task(task, ctx)

    async def test_decompose_propagates_dependencies(self) -> None:
        """Subtask dependencies propagate to created Task objects."""
        task = _make_task()
        plan = _make_plan()
        strategy = ManualDecompositionStrategy(plan)
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)
        ctx = DecompositionContext()

        result = await service.decompose_task(task, ctx)

        tasks_by_id = {t.id: t for t in result.created_tasks}
        assert tasks_by_id[as_uuid("sub-1")].dependencies == ()
        assert tasks_by_id[as_uuid("sub-2")].dependencies == (sid("sub-1"),)
        assert tasks_by_id[as_uuid("sub-3")].dependencies == (sid("sub-2"),)

    def test_rollup_status_delegates(self) -> None:
        """rollup_status delegates to StatusRollup.compute."""
        plan = _make_plan()
        strategy = ManualDecompositionStrategy(plan)
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)

        rollup = service.rollup_status(
            "task-svc-1",
            (
                TaskStatus.COMPLETED,
                TaskStatus.COMPLETED,
                TaskStatus.COMPLETED,
            ),
        )
        assert rollup.derived_parent_status == TaskStatus.COMPLETED


class TestOneDecompositionCannotRunForever:
    """A planner waiting on a provider that never answers holds its caller.

    Two of the four callers are request handlers, so an unbounded call
    occupies an HTTP worker for as long as the provider stalls. The ceiling
    lives on the service because that is the one place every caller comes
    through, and a per-caller answer is a second owner of the same number.
    """

    async def test_a_planner_that_never_answers_is_abandoned(self) -> None:
        never = _NeverAnsweringStrategy()
        resolver: MagicMock = mock_of[ConfigResolverProtocol]()
        resolver.get_float.return_value = _A_SHORT_CEILING
        resolver.get_bool.return_value = False
        service = DecompositionService(
            never,
            TaskStructureClassifier(),
            config_resolver=resolver,
        )

        with pytest.raises(DecompositionError):
            await service.decompose_task(_make_task(), DecompositionContext())

    async def test_the_ceiling_is_read_per_decomposition(self) -> None:
        # Captured at construction it would take a restart to apply, which
        # for a ceiling an operator raises because their provider is slow is
        # the moment it is least available to them.
        resolver: MagicMock = mock_of[ConfigResolverProtocol]()
        resolver.get_float.return_value = _A_GENEROUS_CEILING
        resolver.get_bool.return_value = False
        service = DecompositionService(
            ManualDecompositionStrategy(_make_plan()),
            TaskStructureClassifier(),
            config_resolver=resolver,
        )
        ctx = DecompositionContext()

        await service.decompose_task(_make_task(), ctx)
        await service.decompose_task(_make_task(), ctx)

        assert resolver.get_float.await_count == 2

    async def test_an_unreadable_ceiling_still_bounds_the_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The failure this exists to prevent is an unbounded wait, so a
        # settings read that failed is no reason to grant one. Driven by the
        # strategy that never answers: against one that returns immediately
        # the assertion holds whether the fallback bounds anything or removes
        # the ceiling altogether, which is no test of the fallback at all.
        monkeypatch.setattr(
            service_module,
            "_DEFAULT_DECOMPOSITION_TIMEOUT_SECONDS",
            _A_SHORT_CEILING,
        )
        resolver: MagicMock = mock_of[ConfigResolverProtocol]()
        resolver.get_float.side_effect = RuntimeError("settings unreadable")
        service = DecompositionService(
            _NeverAnsweringStrategy(),
            TaskStructureClassifier(),
            config_resolver=resolver,
        )

        with pytest.raises(DecompositionError):
            await service.decompose_task(_make_task(), DecompositionContext())

    async def test_no_resolver_at_all_still_bounds_the_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The other way the read can be absent: a harness runs with no
        # settings, and the bound has to stand there too.
        monkeypatch.setattr(
            service_module,
            "_DEFAULT_DECOMPOSITION_TIMEOUT_SECONDS",
            _A_SHORT_CEILING,
        )
        service = DecompositionService(
            _NeverAnsweringStrategy(),
            TaskStructureClassifier(),
        )

        with pytest.raises(DecompositionError):
            await service.decompose_task(_make_task(), DecompositionContext())


class _ContextRecordingStrategy:
    """Captures the context the service actually hands the planner."""

    def __init__(self) -> None:
        self.seen: DecompositionContext | None = None

    async def decompose(
        self,
        task: Task,
        context: DecompositionContext,
    ) -> DecompositionPlan:
        self.seen = context
        return _make_plan(parent_task_id=str(task.id))

    def get_strategy_name(self) -> str:
        return "context-recording"

    def plans_any_task(self) -> bool:
        return True


class _StubInventory:
    """A workspace inventory that answers without touching a disk."""

    def __init__(self, answer: str = "server.js, index.html") -> None:
        self.answer = answer
        self.asked_for: list[str] = []

    async def describe_inventory(self, project_id: str) -> str:
        self.asked_for.append(project_id)
        return self.answer


class TestPlanningIsGroundedInTheRealWorkspace:
    """Every decomposition path must be told what the project actually holds.

    Two of the five context-construction sites live deep in the engine with no
    workspace root to read, and the charter route is one of them, so the fact
    is resolved at the single seam they all pass through instead.
    """

    async def test_the_planner_is_told_what_the_workspace_holds(self) -> None:
        strategy = _ContextRecordingStrategy()
        inventory = _StubInventory()
        service = DecompositionService(
            strategy, TaskStructureClassifier(), workspace_inventory=inventory
        )

        await service.decompose_task(_make_task(), DecompositionContext())

        assert strategy.seen is not None
        assert strategy.seen.workspace_summary == "server.js, index.html"

    async def test_the_inventory_is_asked_about_this_task_s_project(self) -> None:
        strategy = _ContextRecordingStrategy()
        inventory = _StubInventory()
        service = DecompositionService(
            strategy, TaskStructureClassifier(), workspace_inventory=inventory
        )

        await service.decompose_task(_make_task(), DecompositionContext())

        assert inventory.asked_for == ["proj-1"]

    async def test_a_caller_supplied_summary_is_not_overwritten(self) -> None:
        strategy = _ContextRecordingStrategy()
        inventory = _StubInventory()
        service = DecompositionService(
            strategy, TaskStructureClassifier(), workspace_inventory=inventory
        )

        await service.decompose_task(
            _make_task(), DecompositionContext(workspace_summary="a known answer")
        )

        assert strategy.seen is not None
        assert strategy.seen.workspace_summary == "a known answer"
        assert inventory.asked_for == []

    async def test_no_inventory_wired_leaves_the_context_alone(self) -> None:
        """A harness builds this service with no workspace at all."""
        strategy = _ContextRecordingStrategy()
        service = DecompositionService(strategy, TaskStructureClassifier())

        await service.decompose_task(_make_task(), DecompositionContext())

        assert strategy.seen is not None
        assert strategy.seen.workspace_summary is None
