"""Tests for the decomposition recursion point.

``current_depth`` was declared, read by three strategies and by the planning
prompt, and written by nothing, so every decomposition the product ever ran
believed it was at the root and an oversized item was dispatched whole. These
cover the write, the split it enables, and the two ways it must stop.
"""

from typing import override
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskStructure, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.atomicity import (
    DEPTH_BACKSTOP,
    PLANNER_DECLINED,
    SESSIONS_BACKSTOP,
)
from synthorg.engine.decomposition.classifier import TaskStructureClassifier
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.errors import (
    DecompositionError,
    DecompositionUnsplittableError,
)
from synthorg.settings.errors import SettingNotFoundError
from tests._shared import as_uuid, sid
from tests.unit.engine.decomposition._doubles import (
    Bounds,
    ScriptedStrategy,
)
from tests.unit.engine.decomposition._doubles import (
    config_resolver as scripted_resolver,
)

pytestmark = pytest.mark.unit

#: Long enough that no case here races the wall-clock ceiling.
_A_GENEROUS_CEILING = 60.0

#: One deliverable per unit, matching the shipped default, so a subtask
#: declaring two is oversized and one declaring a single one is not.
_MAX_ARTIFACTS = 1

#: Well above anything declared below, so only the artefact rule ever fires
#: and a case that splits is unambiguous about why.
_MAX_CRITERIA = 20

#: The depth backstop these cases run under. Small, because what each case is
#: about is where recursion stops, and a generous backstop would need a tree
#: nobody here is building to reach it.
_MAX_DEPTH = 3

#: Width and whole-tree session backstops, both set well clear of anything
#: below so neither is what any case is measuring.
_MAX_SUBTASKS = 10
_MAX_TREE_SESSIONS = 40


_BOUNDS = Bounds(
    ceiling=_A_GENEROUS_CEILING,
    artifacts=_MAX_ARTIFACTS,
    criteria=_MAX_CRITERIA,
    depth=_MAX_DEPTH,
    subtasks=_MAX_SUBTASKS,
    tree_sessions=_MAX_TREE_SESSIONS,
)


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


def _two_level_service(
    *, recursion_enabled: bool, tree_sessions: int = _MAX_TREE_SESSIONS
) -> tuple[DecompositionService, ScriptedStrategy]:
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
    strategy = ScriptedStrategy({str(as_uuid("root")): root, sid("big"): below})
    service = DecompositionService(
        strategy,
        TaskStructureClassifier(),
        config_resolver=scripted_resolver(
            _BOUNDS, recursion_enabled=recursion_enabled, tree_sessions=tree_sessions
        ),
    )
    return service, strategy


def _result(
    parent: str,
    *,
    subtasks: tuple[SubtaskDefinition, ...],
    depth: int = 0,
    children: tuple[DecompositionResult, ...] = (),
) -> DecompositionResult:
    """Build a level of a tree directly, bypassing the service.

    Returns:
        The level.
    """
    return DecompositionResult(
        plan=_plan(parent, subtasks),
        # The created task's id IS its definition's id: the model refuses a
        # level where the two sets differ, which is what makes the id a
        # guaranteed bijection everywhere the tree is walked.
        created_tasks=tuple(
            _task(str(definition.id)).model_copy(update={"id": UUID(definition.id)})
            for definition in subtasks
        ),
        dependency_edges=(),
        depth=depth,
        children=children,
    )


class TestATreeCannotMisdescribeItsOwnShape:
    """A child hanging off nothing would be dispatched twice, container and all.

    Asserted on the model rather than through the service, because the service
    can only build a well-formed tree and the validator is what stands between
    a hand-built or deserialised one and the dispatcher.
    """

    def test_a_child_naming_an_unknown_parent_is_refused(self) -> None:
        stray = _result(sid("nobody"), subtasks=(_subtask("x", artifacts=1),), depth=1)

        with pytest.raises(ValueError, match="which is not one of this level's tasks"):
            _result(
                str(as_uuid("root")),
                subtasks=(_subtask("a", artifacts=1),),
                children=(stray,),
            )

    def test_two_children_naming_one_parent_are_refused(self) -> None:
        # The second would overwrite the first everywhere the tree is keyed on
        # the parent, silently losing a whole subtree.
        parent_id = sid("a")
        first = _result(parent_id, subtasks=(_subtask("x", artifacts=1),), depth=1)
        second = _result(parent_id, subtasks=(_subtask("y", artifacts=1),), depth=1)

        with pytest.raises(ValueError, match="both name parent"):
            _result(
                str(as_uuid("root")),
                subtasks=(_subtask("a", artifacts=1),),
                children=(first, second),
            )

    def test_a_child_at_the_wrong_depth_is_refused(self) -> None:
        # Depths must be dense: max_depth_reached maxes over them, and a gap
        # would report a tree shallower than it is.
        parent_id = sid("a")
        skipped = _result(parent_id, subtasks=(_subtask("x", artifacts=1),), depth=2)

        with pytest.raises(ValueError, match="expected 1"):
            _result(
                str(as_uuid("root")),
                subtasks=(_subtask("a", artifacts=1),),
                children=(skipped,),
            )


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


class TestAContainerIsDispatchedAsItsAssembly:
    """Wave building dispatches the WHOLE tree, so a container runs too.

    Whether it runs its own children's work over again, or assembles what they
    delivered, is decided here and not on the projection path: this is the one
    a ``coordinate()`` call with no reviewed plan takes.
    """

    async def _tree(self) -> DecompositionResult:
        """Decompose a root whose first unit splits.

        Returns:
            The two-level tree.
        """
        service, _ = _two_level_service(recursion_enabled=True)
        return await service.decompose_task(
            _task("root"), DecompositionContext(max_depth=2)
        )

    def _container(self, tree: DecompositionResult) -> SubtaskDefinition:
        """Find the container definition in *tree*'s root level.

        Returns:
            The definition of the unit that split.
        """
        return next(unit for unit in tree.plan.subtasks if unit.id == sid("big"))

    async def test_the_container_task_is_briefed_to_assemble_its_children(
        self,
    ) -> None:
        tree = await self._tree()

        container = next(task for task in tree.all_tasks if str(task.id) == sid("big"))

        assert "Assemble the delivered work" in container.description
        assert "Unit big-a" in container.description
        assert "Unit big-b" in container.description

    async def test_the_container_declares_its_subtree_s_own_evidence(self) -> None:
        tree = await self._tree()

        container = next(task for task in tree.all_tasks if str(task.id) == sid("big"))

        declared = {artifact.path for artifact in container.artifacts_expected}
        assert any(
            path.startswith(".synthorg/integration/") and path.endswith("report.md")
            for path in declared
        )

    async def test_a_leaf_is_left_as_the_work_it_is(self) -> None:
        tree = await self._tree()

        leaf = next(task for task in tree.all_tasks if str(task.id) == sid("small"))

        assert "Assemble the delivered work" not in leaf.description

    async def test_routing_and_dispatch_read_one_stakes_verdict(self) -> None:
        # Routing admits candidates against the DEFINITION and dispatch judges
        # the TASK. Escalating only one routes an assembly to an agent the
        # other then refuses, and the escalation reaches no selection at all.
        tree = await self._tree()

        container_task = next(
            task for task in tree.all_tasks if str(task.id) == sid("big")
        )

        assert self._container(tree).stakes is container_task.stakes


class TestAStrategyThatPlansOneTaskIsNotRecursedInto:
    """A working endpoint must not start failing because depth was enabled."""

    async def test_the_manual_strategy_is_left_flat(self) -> None:
        # ManualDecompositionStrategy holds one operator-supplied plan for one
        # parent and raises when asked about anything else. Recursing into it
        # turns an oversized subtask into a failed REQUEST, so the manual
        # endpoint would refuse every plan whose subtask declares two artifacts
        # the moment an operator enabled recursion.
        from synthorg.engine.decomposition.manual import ManualDecompositionStrategy

        plan = _plan(
            str(as_uuid("root")),
            (_subtask("big", artifacts=_MAX_ARTIFACTS + 5),),
        )
        service = DecompositionService(
            ManualDecompositionStrategy(plan),
            TaskStructureClassifier(),
            config_resolver=scripted_resolver(_BOUNDS, recursion_enabled=True),
        )

        result = await service.decompose_task(
            _task("root"), DecompositionContext(max_depth=4)
        )

        assert result.children == ()
        assert len(result.created_tasks) == 1


class TestRecursionStops:
    """What leaves a decomposition flat, and what refuses to be one.

    The operator's own answers stop it silently on purpose: the switch off,
    or a depth budget with no room. A switch whose definition cannot answer
    stops it too, because unreadable stays unreadable. A settings store that
    is momentarily down is none of those and surfaces, since recursion ships
    on and swallowing that reading plans the whole objective at one level.
    """

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

    async def test_a_switch_that_cannot_answer_for_itself_leaves_it_flat(
        self,
    ) -> None:
        # A switch whose own definition cannot answer is unreadable for as
        # long as it stays that way, so off is the only reading that cannot
        # spend a planning session per node on an instruction nobody gave.
        resolver = scripted_resolver(
            _BOUNDS,
            bool_error=SettingNotFoundError(
                "coordination/recursive_decomposition_enabled"
            ),
        )
        service = self._service_over(resolver)

        result = await service.decompose_task(
            _task("root"), DecompositionContext(max_depth=4)
        )

        assert result.children == ()

    async def test_a_settings_store_that_is_down_is_not_a_silent_downgrade(
        self,
    ) -> None:
        # Recursion ships ON, so swallowing this plans every objective at one
        # level for as long as the store stays down, with one WARNING per
        # decomposition and no other sign. A store that is momentarily
        # unreachable is a fact about the moment, not about the setting.
        resolver = scripted_resolver(
            _BOUNDS, bool_error=RuntimeError("settings backend is gone")
        )
        service = self._service_over(resolver)

        with pytest.raises(RuntimeError, match="settings backend is gone"):
            await service.decompose_task(
                _task("root"), DecompositionContext(max_depth=4)
            )

    def _service_over(self, resolver: MagicMock) -> DecompositionService:
        """Build the service over a root level holding one oversized unit.

        Returns:
            The service, which recurses whenever the switch says it may.
        """
        root = _plan(
            str(as_uuid("root")),
            (_subtask("big", artifacts=_MAX_ARTIFACTS + 5),),
        )
        return DecompositionService(
            ScriptedStrategy({str(as_uuid("root")): root}),
            TaskStructureClassifier(),
            config_resolver=resolver,
        )

    async def test_no_resolver_at_all_leaves_the_result_flat(self) -> None:
        # A harness runs with no settings backend, and the answer has to stand
        # there too.
        root = _plan(
            str(as_uuid("root")),
            (_subtask("big", artifacts=_MAX_ARTIFACTS + 5),),
        )
        service = DecompositionService(
            ScriptedStrategy({str(as_uuid("root")): root}),
            TaskStructureClassifier(),
        )

        result = await service.decompose_task(
            _task("root"), DecompositionContext(max_depth=4)
        )

        assert result.children == ()


def _definition(subtask_id: str, result: DecompositionResult) -> SubtaskDefinition:
    """Return the definition *subtask_id* ended up with on *result*'s plan.

    Returns:
        The definition, carrying whatever unsplit reason the service stamped.

    Raises:
        AssertionError: The level does not hold that subtask, which means the
            walk went somewhere the case did not intend.
    """
    for definition in result.plan.subtasks:
        if definition.id == subtask_id:
            return definition
    msg = f"{subtask_id!r} is not one of this level's subtasks"
    raise AssertionError(msg)


class TestTheTreeBudgetStopsGracefully:
    """The whole-tree session budget, and why it is not the wall-clock one.

    A tree that runs out of sessions has already paid for the levels it
    planned, so it hands them back and says which units it could not split.
    The wall-clock ceiling raises and throws all of it away, which is why this
    one exists beside it rather than instead of it.
    """

    async def test_a_spent_budget_returns_the_tree_it_already_planned(self) -> None:
        # One session: the root claims it, so nothing is left to open a child.
        service, strategy = _two_level_service(recursion_enabled=True, tree_sessions=1)

        result = await service.decompose_task(
            _task("root"), DecompositionContext(max_depth=_MAX_DEPTH)
        )

        assert result.children == ()
        assert strategy.seen_depths == [0]
        assert len(result.created_tasks) == 2

    async def test_the_unit_it_could_not_split_says_which_bound_stopped_it(
        self,
    ) -> None:
        # The operator reading the plan is the one who can raise the budget,
        # and a container log is not where they look.
        service, _ = _two_level_service(recursion_enabled=True, tree_sessions=1)

        result = await service.decompose_task(
            _task("root"), DecompositionContext(max_depth=_MAX_DEPTH)
        )

        reason = _definition(sid("big"), result).unsplit_reason
        assert reason is not None
        assert SESSIONS_BACKSTOP in reason

    async def test_a_unit_that_was_never_oversized_carries_no_reason(self) -> None:
        service, _ = _two_level_service(recursion_enabled=True, tree_sessions=1)

        result = await service.decompose_task(
            _task("root"), DecompositionContext(max_depth=_MAX_DEPTH)
        )

        assert _definition(sid("small"), result).unsplit_reason is None

    async def test_the_depth_backstop_reports_itself_rather_than_the_budget(
        self,
    ) -> None:
        # Two backstops answered by two different operator actions, so a unit
        # that went unsplit has to name which one bound.
        service, _ = _two_level_service(recursion_enabled=True)

        result = await service.decompose_task(
            _task("root"), DecompositionContext(max_depth=1)
        )

        reason = _definition(sid("big"), result).unsplit_reason
        assert reason is not None
        assert DEPTH_BACKSTOP in reason


class _RefusingStrategy(ScriptedStrategy):
    """Plans the root, then fails every child level with *failure*."""

    def __init__(
        self, plans: dict[str, DecompositionPlan], *, failure: Exception
    ) -> None:
        super().__init__(plans)
        self._failure = failure

    @override
    async def decompose(
        self, task: Task, context: DecompositionContext
    ) -> DecompositionPlan:
        """Answer the scripted plan, or fail where none was scripted.

        Returns:
            The scripted plan.

        Raises:
            Exception: The configured failure, for any unscripted task.
        """
        self.seen_depths.append(context.current_depth)
        plan = self._plans.get(str(task.id))
        if plan is None:
            raise self._failure
        return plan


def _service_whose_child_level_fails(failure: Exception) -> DecompositionService:
    """Build a service whose root splits and whose child planning fails.

    Returns:
        The service.
    """
    root = _plan(
        str(as_uuid("root")),
        (
            _subtask("big", artifacts=_MAX_ARTIFACTS + 1),
            _subtask("small", artifacts=_MAX_ARTIFACTS),
        ),
    )
    return DecompositionService(
        _RefusingStrategy({str(as_uuid("root")): root}, failure=failure),
        TaskStructureClassifier(),
        config_resolver=scripted_resolver(_BOUNDS, recursion_enabled=True),
    )


class TestAPlannerThatCannotComplyEndsOnThePlan:
    """The terminal end of the last-level correction.

    Its own retries are spent inside the child's strategy. What the level that
    ASKED for that child does with the exhaustion is the whole question: its
    own plan is valid, so discarding it would throw away every level already
    paid for in order to report one unit's size.
    """

    async def test_the_level_that_asked_keeps_its_plan(self) -> None:
        service = _service_whose_child_level_fails(
            DecompositionUnsplittableError("still three deliverables")
        )

        result = await service.decompose_task(
            _task("root"), DecompositionContext(max_depth=_MAX_DEPTH)
        )

        assert {str(task.id) for task in result.leaf_tasks} == {
            sid("big"),
            sid("small"),
        }

    async def test_the_unit_says_the_planner_declined(self) -> None:
        service = _service_whose_child_level_fails(
            DecompositionUnsplittableError("still three deliverables")
        )

        result = await service.decompose_task(
            _task("root"), DecompositionContext(max_depth=_MAX_DEPTH)
        )

        reason = _definition(sid("big"), result).unsplit_reason
        assert reason is not None
        assert PLANNER_DECLINED in reason

    async def test_a_unit_that_was_never_oversized_still_carries_no_reason(
        self,
    ) -> None:
        service = _service_whose_child_level_fails(
            DecompositionUnsplittableError("still three deliverables")
        )

        result = await service.decompose_task(
            _task("root"), DecompositionContext(max_depth=_MAX_DEPTH)
        )

        assert _definition(sid("small"), result).unsplit_reason is None

    async def test_every_other_child_failure_still_surfaces(self) -> None:
        # The type is what keeps the catch above from being a swallow: a
        # transport that kept mangling replies is fixed at the provider, and
        # filing it as a note on one plan item hides an outage.
        service = _service_whose_child_level_fails(
            DecompositionError("retries exhausted: malformed JSON")
        )

        with pytest.raises(DecompositionError) as caught:
            await service.decompose_task(
                _task("root"), DecompositionContext(max_depth=_MAX_DEPTH)
            )

        assert not isinstance(caught.value, DecompositionUnsplittableError)


class TestASmallObjectiveStaysSmall:
    """Less if it needs less: the backstops are guards, not targets.

    Recursion on by default is only defensible if an objective that is already
    one agent's work per unit costs exactly what it cost before.
    """

    async def test_nothing_oversized_plans_once_and_stays_flat(self) -> None:
        root = _plan(
            str(as_uuid("root")),
            (
                _subtask("one", artifacts=_MAX_ARTIFACTS),
                _subtask("two", artifacts=_MAX_ARTIFACTS),
            ),
        )
        strategy = ScriptedStrategy({str(as_uuid("root")): root})
        service = DecompositionService(
            strategy,
            TaskStructureClassifier(),
            config_resolver=scripted_resolver(_BOUNDS, recursion_enabled=True),
        )

        result = await service.decompose_task(
            _task("root"), DecompositionContext(max_depth=_MAX_DEPTH)
        )

        assert strategy.seen_depths == [0]
        assert result.children == ()
        assert result.max_depth_reached == 0
        assert all(
            definition.unsplit_reason is None for definition in result.plan.subtasks
        )
