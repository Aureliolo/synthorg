# module-kind: tests
"""The objective's criteria reach every level of a tree, narrowed on the way.

The size signal is documented as self-terminating because "a unit claiming one
criterion becomes a task with one acceptance criterion, so its own children can
claim at most that one". That held only if the criteria a level is answerable
for descend with it, and they did not: a child task carried the planner's own
per-item prose, so every level invented a fresh vocabulary and a claim made
below the root named nothing the objective had ever stated.

The integration cases at the bottom are the ones a live sweep could not make:
they assert that a claim made below the root is still a ROOT criterion,
verbatim, and that a unit claiming one criterion ends its own branch.
"""

from unittest.mock import MagicMock

import pytest

from synthorg.core.plan_tree import SubtreeStep
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskStructure, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition._recursion import (
    child_context,
    stamp_objective_criteria,
)
from synthorg.engine.decomposition.classifier import TaskStructureClassifier
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import as_uuid, mock_of, sid

pytestmark = pytest.mark.unit

#: The root objective's own criteria, which is the vocabulary every level of
#: the tree must still be claiming from however deep it goes.
_ROOT_CRITERIA: tuple[NotBlankStr, ...] = (
    NotBlankStr("R01: The header row names the columns"),
    NotBlankStr("R02: An integer column compares and sorts numerically"),
    NotBlankStr("R03: A decimal column reads as a float"),
    NotBlankStr("R04: An empty field is NULL"),
)

#: Long enough that nothing here races the wall-clock ceiling.
_A_GENEROUS_CEILING = 60.0

#: Opened wide so only the claim count decides a split, which is the rule the
#: inheritance makes true.
_MAX_ARTIFACTS = 20
_MAX_CRITERIA = 20
_MAX_DEPTH = 4
_MAX_SUBTASKS = 10
_MAX_TREE_SESSIONS = 40


def _resolver() -> MagicMock:
    """Build a settings resolver answering every key the service reads.

    Returns:
        The scripted resolver, with recursion on.
    """
    resolver: MagicMock = mock_of[ConfigResolverProtocol]()
    resolver.get_float.return_value = _A_GENEROUS_CEILING
    resolver.get_bool.return_value = True
    resolver.get_int.side_effect = lambda _namespace, key: {
        "subtask_max_artifacts": _MAX_ARTIFACTS,
        "subtask_max_criteria": _MAX_CRITERIA,
        "decomposition_max_depth": _MAX_DEPTH,
        "decomposition_max_subtasks": _MAX_SUBTASKS,
        "decomposition_tree_max_sessions": _MAX_TREE_SESSIONS,
    }[key]
    return resolver


def _task(label: str, *, criteria: tuple[NotBlankStr, ...]) -> Task:
    """Build a task declaring *criteria*.

    Returns:
        The task.
    """
    return Task(
        id=as_uuid(label),
        title=NotBlankStr(f"Objective {label}"),
        description=NotBlankStr("Deliver the thing"),
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=NotBlankStr("proj-inheritance"),
        created_by=NotBlankStr("operator"),
        status=TaskStatus.CREATED,
        acceptance_criteria=tuple(
            AcceptanceCriterion(description=criterion) for criterion in criteria
        ),
    )


def _subtask(label: str, *, satisfies: tuple[NotBlankStr, ...]) -> SubtaskDefinition:
    """Build one subtask claiming *satisfies*.

    Its own acceptance criteria are deliberately the planner's own prose,
    naming no objective criterion, because that is what a real planner writes
    and what used to become the next level's whole vocabulary.

    Returns:
        The definition.
    """
    return SubtaskDefinition(
        id=NotBlankStr(sid(label)),
        title=NotBlankStr(f"Unit {label}"),
        description=NotBlankStr(f"Build {label}"),
        expected_artifacts=(NotBlankStr(f"src/{label}.py"),),
        acceptance_criteria=(NotBlankStr(f"{label} works and its tests pass"),),
        satisfies=satisfies,
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
    """Answers with a plan per parent task, recording the contexts it saw."""

    def __init__(self, plans: dict[str, DecompositionPlan]) -> None:
        self._plans = plans
        self.seen: list[tuple[int, tuple[NotBlankStr, ...]]] = []

    async def decompose(
        self, task: Task, context: DecompositionContext
    ) -> DecompositionPlan:
        """Return the plan scripted for *task*.

        Returns:
            The scripted plan.

        Raises:
            AssertionError: The recursion walked somewhere unscripted.
        """
        self.seen.append((context.current_depth, context.objective_criteria))
        plan = self._plans.get(str(task.id))
        if plan is None:
            msg = f"strategy asked for an unscripted task {task.id!r}"
            raise AssertionError(msg)
        return plan

    def plans_any_task(self) -> bool:
        """Answer for a strategy keyed by parent.

        Returns:
            ``True``.
        """
        return True

    def get_strategy_name(self) -> str:
        """Name this strategy for the service's logs.

        Returns:
            The strategy name.
        """
        return "scripted"


def _claims(result: DecompositionResult) -> dict[int, set[str]]:
    """Every claim made at every level of *result*, keyed by depth.

    Returns:
        Depth mapped to the claims its subtasks made.
    """
    claimed: dict[int, set[str]] = {
        result.depth: {
            str(claim) for sub in result.plan.subtasks for claim in sub.satisfies
        }
    }
    for child in result.children:
        for depth, values in _claims(child).items():
            claimed.setdefault(depth, set()).update(values)
    return claimed


class TestTheRootStamp:
    def test_an_unstamped_context_takes_the_objective_criteria(self) -> None:
        task = _task("root", criteria=_ROOT_CRITERIA)

        stamped = stamp_objective_criteria(task, DecompositionContext())

        assert stamped.objective_criteria == _ROOT_CRITERIA

    def test_a_declared_vocabulary_is_left_alone(self) -> None:
        """One resolver, ordered: a caller that declared its own keeps it."""
        task = _task("root", criteria=_ROOT_CRITERIA)
        declared = DecompositionContext(objective_criteria=(_ROOT_CRITERIA[0],))

        stamped = stamp_objective_criteria(task, declared)

        assert stamped.objective_criteria == (_ROOT_CRITERIA[0],)

    def test_an_objective_declaring_none_stamps_nothing(self) -> None:
        task = _task("root", criteria=())

        stamped = stamp_objective_criteria(task, DecompositionContext())

        assert stamped.objective_criteria == ()


class TestTheNarrowing:
    def test_a_child_takes_exactly_what_its_parent_claimed(self) -> None:
        parent = DecompositionContext(objective_criteria=_ROOT_CRITERIA)

        child = child_context(
            parent,
            step=SubtreeStep(title="Ingest", position=0),
            satisfied=(_ROOT_CRITERIA[2], _ROOT_CRITERIA[0]),
        )

        assert child.objective_criteria == (_ROOT_CRITERIA[0], _ROOT_CRITERIA[2])

    def test_the_parent_spelling_survives_a_reworded_claim(self) -> None:
        """Carrying the CLAIM's spelling would drift the vocabulary per level."""
        parent = DecompositionContext(objective_criteria=_ROOT_CRITERIA)

        child = child_context(
            parent,
            step=SubtreeStep(title="Ingest", position=0),
            satisfied=(NotBlankStr("  r01:  THE HEADER ROW names the columns "),),
        )

        assert child.objective_criteria == (_ROOT_CRITERIA[0],)

    def test_a_unit_claiming_nothing_hands_down_nothing(self) -> None:
        """A pure-support subtree advances no objective criterion, honestly."""
        parent = DecompositionContext(objective_criteria=_ROOT_CRITERIA)

        child = child_context(
            parent, step=SubtreeStep(title="Choose", position=0), satisfied=()
        )

        assert child.objective_criteria == ()


class TestTheInductionHolds:
    async def test_a_claim_below_the_root_is_still_a_root_criterion(self) -> None:
        """The regression a live sweep could not see.

        A claim made a level below the root names a criterion the ROOT
        objective states, so it resolves against the specification rather than
        against whatever prose the level above happened to write.
        """
        root = _plan(
            str(as_uuid("root")),
            (
                _subtask("ingest", satisfies=_ROOT_CRITERIA[:2]),
                _subtask("typing", satisfies=_ROOT_CRITERIA[2:]),
            ),
        )
        ingest = _plan(
            sid("ingest"),
            (
                _subtask("header", satisfies=(_ROOT_CRITERIA[0],)),
                _subtask("ints", satisfies=(_ROOT_CRITERIA[1],)),
            ),
        )
        typing_ = _plan(
            sid("typing"),
            (
                _subtask("floats", satisfies=(_ROOT_CRITERIA[2],)),
                _subtask("nulls", satisfies=(_ROOT_CRITERIA[3],)),
            ),
        )
        strategy = _ScriptedStrategy(
            {
                str(as_uuid("root")): root,
                sid("ingest"): ingest,
                sid("typing"): typing_,
            }
        )
        service = DecompositionService(
            strategy, TaskStructureClassifier(), config_resolver=_resolver()
        )

        result = await service.decompose_task(
            _task("root", criteria=_ROOT_CRITERIA), DecompositionContext()
        )

        assert _claims(result)[1] == {str(one) for one in _ROOT_CRITERIA}

    async def test_each_level_is_answerable_for_what_its_parent_claimed(self) -> None:
        root = _plan(
            str(as_uuid("root")),
            (_subtask("ingest", satisfies=_ROOT_CRITERIA[:2]),),
        )
        ingest = _plan(
            sid("ingest"),
            (
                _subtask("header", satisfies=(_ROOT_CRITERIA[0],)),
                _subtask("ints", satisfies=(_ROOT_CRITERIA[1],)),
            ),
        )
        strategy = _ScriptedStrategy(
            {str(as_uuid("root")): root, sid("ingest"): ingest}
        )
        service = DecompositionService(
            strategy, TaskStructureClassifier(), config_resolver=_resolver()
        )

        await service.decompose_task(
            _task("root", criteria=_ROOT_CRITERIA), DecompositionContext()
        )

        assert strategy.seen == [
            (0, _ROOT_CRITERIA),
            (1, _ROOT_CRITERIA[:2]),
        ]

    async def test_the_tree_stops_where_every_unit_claims_one_criterion(self) -> None:
        """Self-termination, which the size signal's own docstring promises.

        The depth backstop is four and the session budget is forty, so nothing
        but the claim count stops this: a unit claiming one criterion is
        atomic, and the level below it is never planned.
        """
        root = _plan(
            str(as_uuid("root")),
            (_subtask("ingest", satisfies=_ROOT_CRITERIA[:2]),),
        )
        ingest = _plan(
            sid("ingest"),
            (
                _subtask("header", satisfies=(_ROOT_CRITERIA[0],)),
                _subtask("ints", satisfies=(_ROOT_CRITERIA[1],)),
            ),
        )
        strategy = _ScriptedStrategy(
            {str(as_uuid("root")): root, sid("ingest"): ingest}
        )
        service = DecompositionService(
            strategy, TaskStructureClassifier(), config_resolver=_resolver()
        )

        result = await service.decompose_task(
            _task("root", criteria=_ROOT_CRITERIA), DecompositionContext()
        )

        assert [depth for depth, _ in strategy.seen] == [0, 1]
        assert result.children[0].children == ()
