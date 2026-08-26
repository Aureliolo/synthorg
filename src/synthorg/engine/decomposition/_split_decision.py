# module-kind: code
"""Whether one unit gets its own planning session, and what it says if not.

The single owner of that question. Three answers, and the two that decline are
not the same: a unit that is already one agent's work is simply left alone,
while a unit that is NOT and still cannot be split has hit a bound the
operator can move, so it carries a reason the plan shows them.

Separated from the walk that acts on it because the walk is recursive and this
is not: a pure verdict per unit is readable and testable on its own, and the
recursion beside it stays a loop over verdicts rather than a loop with four
exits.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum, auto

from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.assembly import Assembly
from synthorg.engine.decomposition._artifacts import expected_artifact_from_spec
from synthorg.engine.decomposition._recursion import RecursionBudget, TreeSessionLedger
from synthorg.engine.decomposition.atomicity import (
    DEPTH_BACKSTOP,
    SESSIONS_BACKSTOP,
    unsplit_reason,
)
from synthorg.engine.decomposition.context import DecompositionContext, depth_budget
from synthorg.engine.decomposition.models import DecompositionResult, SubtaskDefinition
from synthorg.observability import get_logger
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_DEPTH_EXHAUSTED,
    DECOMPOSITION_SUBTASK_OVERSIZED,
    DECOMPOSITION_TREE_SESSIONS_EXHAUSTED,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SplitOutcome:
    """What one level's splitting pass produced.

    Attributes:
        children: The decomposition of each subtask that split.
        unsplit: Each subtask that stayed oversized, mapped to why. Kept
            beside the children rather than logged and dropped, because the
            operator reviewing the plan is the one who can raise the bound or
            narrow the objective, and a container log is not where they look.
        assemblies: Each subtask that DID split, mapped to the assembly it
            dispatches instead of the work below it. A unit that split is a
            container, and the level that split it is the only place that
            fact exists on this path: without it the container keeps the
            oversized description its own children were planned from and runs
            that work a second time, alongside them.
    """

    children: tuple[DecompositionResult, ...]
    unsplit: Mapping[str, str]
    assemblies: Mapping[str, Assembly]


class SplitVerdict(StrEnum):
    """What to do with one of a level's units.

    Three rather than two, because the two that decline are not the same thing
    and the operator can act on only one of them: a unit that is already one
    agent's work needs nothing, while a unit that is NOT and still could not be
    split has hit a bound they can move, and a plan that says only "not split"
    for both hides which.

    Members:
        LEAVE: Dispatch it as it stands; it is one agent's worth of work, or
            a decision, which is chosen rather than decomposed.
        SPLIT: Open a child planning session for it.
        UNSPLIT: It is more than one agent's work and a bound stopped the
            split, so it dispatches anyway carrying the reason.
    """

    LEAVE = auto()
    SPLIT = auto()
    UNSPLIT = auto()


@dataclass(frozen=True, slots=True)
class SplitDecision:
    """The verdict for one unit, and the note it carries when it declines.

    Attributes:
        verdict: What to do.
        reason: What the operator reads on the plan, present exactly on an
            UNSPLIT verdict.
    """

    verdict: SplitVerdict
    reason: str | None = None

    def __post_init__(self) -> None:
        """Hold the verdict and its note to each other.

        Raises:
            ValueError: An UNSPLIT verdict carries no reason, which would put
                a unit on the plan that is visibly oversized and silent about
                why, or a non-UNSPLIT one carries a reason nothing shows.
        """
        if (self.verdict is SplitVerdict.UNSPLIT) != (self.reason is not None):
            msg = (
                f"split verdict {self.verdict.value} with reason={self.reason!r}: "
                f"a unit left oversized names the bound that stopped it, and "
                f"one that was not carries no note"
            )
            raise ValueError(msg)


def decide_split(
    subtask: SubtaskDefinition,
    *,
    task_id: str,
    context: DecompositionContext,
    budget: RecursionBudget,
    ledger: TreeSessionLedger,
) -> SplitDecision:
    """Decide what happens to *subtask* at this level.

    Claims a session from *ledger* on a SPLIT verdict, so the budget is spent
    exactly where the decision to spend it is made.

    Args:
        subtask: The definition to judge.
        task_id: The task built from it, for the log lines.
        context: This level's constraints.
        budget: What may be done about an oversized subtask.
        ledger: The whole tree's remaining planning-session budget.

    Returns:
        The verdict, carrying the operator-facing reason when it declines a
        split it would otherwise have made.
    """
    if subtask.kind is not PlanItemKind.WORK:
        # A DECISION item is a choice among its declared options, not work to
        # divide, and the policy reads only the artifact, criterion and claim
        # counts. One declaring several acceptance criteria would otherwise
        # read as oversized and open a child planning session that plans work
        # nobody asked for, which the harness then tries to build as a leaf.
        return SplitDecision(verdict=SplitVerdict.LEAVE)
    assessment = budget.policy.assess(subtask)
    if not assessment.is_oversized:
        return SplitDecision(verdict=SplitVerdict.LEAVE)
    context_fields = {
        "task_id": task_id,
        "subtask_id": subtask.id,
        "condition": assessment.condition,
        "observed": assessment.observed,
        "limit": assessment.limit,
        "current_depth": context.current_depth,
    }
    if not budget.has_room(context):
        logger.warning(
            DECOMPOSITION_DEPTH_EXHAUSTED,
            max_depth=depth_budget(context),
            **context_fields,
        )
        return SplitDecision(
            verdict=SplitVerdict.UNSPLIT,
            reason=unsplit_reason(assessment, backstop=DEPTH_BACKSTOP),
        )
    if not ledger.take():
        logger.warning(DECOMPOSITION_TREE_SESSIONS_EXHAUSTED, **context_fields)
        return SplitDecision(
            verdict=SplitVerdict.UNSPLIT,
            reason=unsplit_reason(assessment, backstop=SESSIONS_BACKSTOP),
        )
    logger.info(DECOMPOSITION_SUBTASK_OVERSIZED, **context_fields)
    return SplitDecision(verdict=SplitVerdict.SPLIT)


def assembled_subtasks(
    subtasks: Sequence[SubtaskDefinition], outcome: SplitOutcome
) -> tuple[SubtaskDefinition, ...]:
    """Re-describe every definition that split as the assembly it became.

    Args:
        subtasks: This level's definitions, in plan order.
        outcome: What the splitting pass produced.

    Returns:
        The definitions, each container carrying its assembly brief and the
        stakes assembly runs at.
    """
    return tuple(
        subtask
        if (assembly := outcome.assemblies.get(subtask.id)) is None
        else subtask.model_copy(
            update={
                "description": NotBlankStr(assembly.brief),
                "stakes": assembly.stakes,
                "expected_artifacts": (
                    *subtask.expected_artifacts,
                    *(NotBlankStr(path) for path in assembly.paths.declared),
                ),
            }
        )
        for subtask in subtasks
    )


def assembled_task(task: Task, assembly: Assembly | None) -> Task:
    """Re-describe *task* as *assembly*, or leave it as the work it is.

    Its own declarations PLUS the assembly's evidence: the first is what the
    planner said this unit produces, the second is what shows the pieces run
    together, and a probe can only credit a path it was given.

    Args:
        task: The task built from the definition.
        assembly: What it assembles, or ``None`` when it split nothing.

    Returns:
        The task, unchanged for a leaf.
    """
    if assembly is None:
        return task
    return task.model_copy(
        update={
            "description": NotBlankStr(assembly.brief),
            "stakes": assembly.stakes,
            "artifacts_expected": (
                *task.artifacts_expected,
                *(
                    expected_artifact_from_spec(NotBlankStr(path))
                    for path in assembly.paths.declared
                ),
            ),
        }
    )


__all__ = [
    "SplitDecision",
    "SplitOutcome",
    "SplitVerdict",
    "assembled_subtasks",
    "assembled_task",
    "decide_split",
]
