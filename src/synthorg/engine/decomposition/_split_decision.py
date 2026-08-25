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

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum, auto

from synthorg.core.plan_enums import PlanItemKind
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
    """

    children: tuple[DecompositionResult, ...]
    unsplit: Mapping[str, str]


class SplitVerdict(StrEnum):
    """What to do with one of a level's units.

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


__all__ = ["SplitDecision", "SplitOutcome", "SplitVerdict", "decide_split"]
