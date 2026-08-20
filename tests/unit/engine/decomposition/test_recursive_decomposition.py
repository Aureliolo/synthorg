"""Tests for the decomposition recursion point.

``current_depth`` was declared, read by three strategies and by the planning
prompt, and written by nothing, so every decomposition the product ever ran
believed it was at the root and an oversized item was dispatched whole. These
cover the write, the split it enables, and the two ways it must stop.
"""

from unittest.mock import MagicMock

import pytest

from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskStructure, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.classifier import TaskStructureClassifier
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.models import DecompositionPlan, SubtaskDefinition
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import as_uuid, mock_of, sid

pytestmark = pytest.mark.unit

#: Long enough that no case here races the wall-clock ceiling.
_A_GENEROUS_CEILING = 60.0

#: One deliverable per unit, matching the shipped default, so a subtask
#: declaring two is oversized and one declaring a single one is not.
_MAX_ARTIFACTS = 1

#: Well above anything declared below, so only the artefact rule ever fires
#: and a case that splits is unambiguous about why.
_MAX_CRITERIA = 20


def _resolver(*, recursion_enabled: bool) -> MagicMock:
    """Build a settings resolver answering the three keys the service reads.

    Returns:
        The scripted resolver.
    """
    resolver: MagicMock = mock_of[ConfigResolverProtocol]()
    resolver.get_float.return_value = _A_GENEROUS_CEILING
    resolver.get_bool.return_value = recursion_enabled
    resolver.get_int.side_effect = lambda _namespace, key: {
        "leaf_subtask_threshold": _MAX_ARTIFACTS,
        "subtask_max_criteria": _MAX_CRITERIA,
    }[key]
    return resolver


def _task(label: str) -> Task:
    """Build the parent task a decomposition runs against.

    Returns:
        The task.
    """
    return Task(
        id=as_uuid(label),
        title=NotBlankStr(f"Objective {label}"),
        description=NotBlankStr("Deliver the thing"),
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=NotBlankStr("proj-recursion"),
        created_by=NotBlankStr("operator"),
        status=TaskStatus.CREATED,
    )


def _subtask(label: str, *, artifacts: int) -> SubtaskDefinition:
    """Build one subtask declaring *artifacts* deliverables.

    Returns:
        The definition.
    """
    return SubtaskDefinition(
        id=NotBlankStr(sid(label)),
        title=NotBlankStr(f"Unit {label}"),
        description=NotBlankStr(f"Build {label}"),
        expected_artifacts=tuple(
            NotBlankStr(f"src/{label}_{index}.py") for index in range(artifacts)
        ),
        acceptance_criteria=(NotBlankStr(f"{label} works"),),
    )


def _plan(parent: str, subtasks: tuple[SubtaskDefinition, ...]) -> DecompositionPlan:
    """Build a resolved plan for *parent*.

    Returns:
        The plan.
    """
    return DecompositionPlan(
        parent_task_id=NotBlankStr(parent),
        subtasks=subtasks,
        task_structure=TaskStructure.PARALLEL,
    )


class _ScriptedStrategy:
    """Answers with a different plan per parent task, and records its contexts.

    A recursion test needs a planner that can be asked twice about two
    different tasks, which the manual strategy cannot be: it holds one plan and
    rejects any parent but its own.
    """

    def __init__(self, plans: dict[str, DecompositionPlan]) -> None:
        self._plans = plans
        self.seen_depths: list[int] = []

    async def decompose(
        self, task: Task, context: DecompositionContext
    ) -> DecompositionPlan:
        """Return the plan scripted for *task*.

        Returns:
            The scripted plan.

        Raises:
            AssertionError: The strategy was asked about a task no case
                scripted, which means the recursion walked somewhere the test
                did not intend rather than that the planner failed.
        """
        self.seen_depths.append(context.current_depth)
        plan = self._plans.get(str(task.id))
        if plan is None:
            msg = f"strategy asked for an unscripted task {task.id!r}"
            raise AssertionError(msg)
        return plan

    def get_strategy_name(self) -> str:
        """Name this strategy for the service's logs.

        Returns:
            The strategy name.
        """
        return "scripted"


def _two_level_service(
    *, recursion_enabled: bool
) -> tuple[DecompositionService, _ScriptedStrategy]:
    """Build a service over a root plan whose first unit is oversized.

    Returns:
        The service and its strategy, the latter for its recorded depths.
    """
    root = _plan(
        str(as_uuid("root")),
        (
            _subtask("big", artifacts=_MAX_ARTIFACTS + 1),
            _subtask("small", artifacts=_MAX_ARTIFACTS),
        ),
    )
    below = _plan(
        sid("big"),
        (
            _subtask("big-a", artifacts=_MAX_ARTIFACTS),
            _subtask("big-b", artifacts=_MAX_ARTIFACTS),
        ),
    )
    strategy = _ScriptedStrategy({str(as_uuid("root")): root, sid("big"): below})
    service = DecompositionService(
        strategy,
        TaskStructureClassifier(),
        config_resolver=_resolver(recursion_enabled=recursion_enabled),
    )
    return service, strategy


class TestAnOversizedUnitIsSplitRatherThanDispatched:
    """The behaviour the whole recursion point exists for."""

    async def test_the_oversized_unit_is_decomposed_again(self) -> None:
        service, _ = _two_level_service(recursion_enabled=True)

        result = await service.decompose_task(
            _task("root"), DecompositionContext(max_depth=2)
        )

        assert len(result.children) == 1
        assert result.children[0].plan.parent_task_id == sid("big")

    async def test_the_child_level_plans_one_depth_down(self) -> None:
        # The write that never happened. Read by three strategies and by the
        # planning prompt, all of which were being told depth 0 for ever.
        service, strategy = _two_level_service(recursion_enabled=True)

        await service.decompose_task(_task("root"), DecompositionContext(max_depth=2))

        assert strategy.seen_depths == [0, 1]

    async def test_the_tree_reports_the_depth_it_reached(self) -> None:
        service, _ = _two_level_service(recursion_enabled=True)

        result = await service.decompose_task(
            _task("root"), DecompositionContext(max_depth=2)
        )

        assert result.depth == 0
        assert result.children[0].depth == 1
        assert result.max_depth_reached == 1

    async def test_a_split_container_is_not_dispatched_alongside_its_parts(
        self,
    ) -> None:
        # Running both would do the work twice, and the second run would find
        # the first's output already there and have nothing to deliver.
        service, _ = _two_level_service(recursion_enabled=True)

        result = await service.decompose_task(
            _task("root"), DecompositionContext(max_depth=2)
        )

        leaves = {str(task.id) for task in result.leaf_tasks}
        assert leaves == {sid("small"), sid("big-a"), sid("big-b")}
        assert sid("big") in {str(task.id) for task in result.all_tasks}


class TestRecursionStops:
    """Two ways, and neither may be silent."""

    async def test_the_switch_off_leaves_the_result_flat(self) -> None:
        # The default configuration. Every reader that predates recursion has
        # to keep seeing the list it always saw.
        service, strategy = _two_level_service(recursion_enabled=False)

        result = await service.decompose_task(
            _task("root"), DecompositionContext(max_depth=3)
        )

        assert result.children == ()
        assert strategy.seen_depths == [0]
        assert len(result.leaf_tasks) == len(result.created_tasks) == 2

    async def test_the_depth_budget_stops_the_split(self) -> None:
        # max_depth=1 is one level of planning, which is what every caller got
        # before recursion existed.
        service, strategy = _two_level_service(recursion_enabled=True)

        result = await service.decompose_task(
            _task("root"), DecompositionContext(max_depth=1)
        )

        assert result.children == ()
        assert strategy.seen_depths == [0]

    async def test_an_unreadable_switch_leaves_the_result_flat(self) -> None:
        # An unreadable switch means behaving as the product did before
        # recursion existed, which is the only safe reading of it.
        resolver: MagicMock = mock_of[ConfigResolverProtocol]()
        resolver.get_float.return_value = _A_GENEROUS_CEILING
        resolver.get_bool.side_effect = RuntimeError("settings backend is gone")
        root = _plan(
            str(as_uuid("root")),
            (_subtask("big", artifacts=_MAX_ARTIFACTS + 5),),
        )
        service = DecompositionService(
            _ScriptedStrategy({str(as_uuid("root")): root}),
            TaskStructureClassifier(),
            config_resolver=resolver,
        )

        result = await service.decompose_task(
            _task("root"), DecompositionContext(max_depth=4)
        )

        assert result.children == ()

    async def test_no_resolver_at_all_leaves_the_result_flat(self) -> None:
        # A harness runs with no settings backend, and the answer has to stand
        # there too.
        root = _plan(
            str(as_uuid("root")),
            (_subtask("big", artifacts=_MAX_ARTIFACTS + 5),),
        )
        service = DecompositionService(
            _ScriptedStrategy({str(as_uuid("root")): root}),
            TaskStructureClassifier(),
        )

        result = await service.decompose_task(
            _task("root"), DecompositionContext(max_depth=4)
        )

        assert result.children == ()
