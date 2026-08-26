# module-kind: code
"""Is this subtask one agent's worth of work?

Nothing asked this before. A planner producing a coarse item at the one level
it was allowed to plan at had that item dispatched whole: the agent burned its
turn cap and either failed or landed partial work, and the zero-artifact guard
only catches a run that produced *nothing*, so half-done passed.

The verdict is read off the declaration the planner already makes, with no
extra model call. That is a deliberate ceiling on what this can know, and it is
what keeps the signal cheap, deterministic, reproducible and testable: the same
plan always splits the same way. An agent that has read the code would judge its
own unit better, and no published system has that either; this is the half that
can be built without one.

The sibling question at the objective level is answered by
:class:`~synthorg.engine.pipeline.policy.threshold.LeafThresholdRoutingPolicy`,
off its own ``coordination.leaf_subtask_threshold``. The two read the same KIND
of count and want opposite values, which is why they stopped sharing a setting:
an objective declaring two deliverables is a team's work, while a subtask
declaring two is one agent's.

Of the three rules below, the artifact and criterion counts are loose guards
against a unit that is obviously too large; what actually decides the shape of
a tree is ``satisfies``, because it counts the OBJECTIVE's own success criteria
rather than how verbosely a particular planner writes. It is also
self-terminating: a unit claiming one criterion becomes a task with one
acceptance criterion, so its own children can claim at most that one.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from synthorg.engine.decomposition.models import SubtaskDefinition

#: One objective criterion per unit. A subtask advancing several of them is
#: several units by the plan's own account of itself, which is the cheapest
#: honest size signal available and the one that does not depend on how
#: verbosely a particular planner writes.
MAX_SATISFIED_CRITERIA: Final[int] = 1


class AtomicityVerdict(StrEnum):
    """Whether a subtask is small enough to hand to one agent.

    Members:
        ATOMIC: One agent's worth of work; dispatch it.
        OVERSIZED: More than one agent's worth; decompose it again if the
            depth budget allows.
    """

    ATOMIC = "atomic"
    OVERSIZED = "oversized"


@dataclass(frozen=True)
class AtomicityAssessment:
    """The verdict for one subtask, and the condition that produced it.

    The condition travels with the verdict because a tree that came out deeper
    than expected has two possible explanations (items genuinely too large, or
    a threshold set too low) and only the naming of the condition that fired
    tells them apart.

    Attributes:
        verdict: The answer.
        condition: Which rule fired, or ``None`` on an ATOMIC verdict.
        observed: The count that tripped the rule, or ``None``.
        limit: The limit it tripped, or ``None``.
    """

    verdict: AtomicityVerdict
    condition: str | None = None
    observed: int | None = None
    limit: int | None = None

    def __post_init__(self) -> None:
        """Hold the verdict and its explanation to each other.

        Enforced rather than documented: the whole reason the condition rides
        along is to tell "items too large" apart from "threshold too low", and
        an OVERSIZED verdict that names no condition answers neither. The
        symmetric case matters as much: an ATOMIC verdict carrying a condition
        reads as a rule that fired and was ignored.

        Raises:
            ValueError: The verdict and its explanation disagree.
        """
        explained = self.condition is not None
        if self.is_oversized != explained:
            msg = (
                f"atomicity verdict {self.verdict.value} with "
                f"condition={self.condition!r}: an oversized subtask must name "
                f"the rule that fired, and an atomic one must name none"
            )
            raise ValueError(msg)
        if explained and (self.observed is None or self.limit is None):
            msg = (
                f"atomicity condition {self.condition!r} reports "
                f"observed={self.observed!r} against limit={self.limit!r}: a "
                f"named condition carries both numbers or it cannot be read"
            )
            raise ValueError(msg)

    @property
    def is_oversized(self) -> bool:
        """Whether this subtask needs splitting.

        Returns:
            ``True`` on an OVERSIZED verdict.
        """
        return self.verdict is AtomicityVerdict.OVERSIZED


@dataclass(frozen=True)
class SubtaskAtomicityPolicy:
    """Decides atomicity from a subtask's own declaration.

    Attributes:
        max_expected_artifacts: Deliverables one agent may own, from
            ``coordination.subtask_max_artifacts``.
        max_acceptance_criteria: Ways of being done one agent may own, from
            ``coordination.subtask_max_criteria``.
    """

    max_expected_artifacts: int
    max_acceptance_criteria: int

    def assess(self, subtask: SubtaskDefinition) -> AtomicityAssessment:
        """Judge whether *subtask* is one agent's worth of work.

        The rules are checked in declaration order and the first to fire wins,
        so the reported condition is stable rather than a function of which
        count happens to overshoot furthest.

        Args:
            subtask: The definition the planner produced.

        Returns:
            The assessment, naming the condition when it is oversized.
        """
        checks: tuple[tuple[str, int, int], ...] = (
            (
                "expected_artifacts",
                len(subtask.expected_artifacts),
                self.max_expected_artifacts,
            ),
            (
                "acceptance_criteria",
                len(subtask.acceptance_criteria),
                self.max_acceptance_criteria,
            ),
            ("satisfies", len(subtask.satisfies), MAX_SATISFIED_CRITERIA),
        )
        for condition, observed, limit in checks:
            if observed > limit:
                return AtomicityAssessment(
                    verdict=AtomicityVerdict.OVERSIZED,
                    condition=condition,
                    observed=observed,
                    limit=limit,
                )
        return AtomicityAssessment(verdict=AtomicityVerdict.ATOMIC)


#: What stopped a split, named so the reason on the plan says which bound the
#: operator can move. Three backstops and one refusal, because they are
#: answered differently: raise the depth, raise the tree's session budget,
#: raise the per-session ceiling, or accept a unit the planner could not
#: express any smaller.
DEPTH_BACKSTOP: Final[str] = "the depth backstop was reached"
SESSIONS_BACKSTOP: Final[str] = "the tree's planning budget was spent"
SESSION_CEILING_BACKSTOP: Final[str] = (
    "its planning session outran the per-session wall-clock ceiling"
)
PLANNER_DECLINED: Final[str] = "the planner could not split it further"


def unsplit_reason(assessment: AtomicityAssessment, *, backstop: str) -> str:
    """Phrase why a unit reached the plan still oversized.

    Written for the operator reading the plan rather than for a log: it names
    the rule that fired, both numbers, and which bound stopped the split, so
    the two remedies (raise the bound, or narrow the objective) are both
    visible without opening a container log.

    Args:
        assessment: The verdict that judged the unit oversized.
        backstop: What stopped the split, one of the constants above.

    Returns:
        The reason recorded on the plan item.

    Raises:
        ValueError: The assessment names no condition, so it judged the unit
            atomic and there is nothing to explain.
    """
    if not assessment.is_oversized:
        msg = "an atomic assessment has no unsplit reason to give"
        raise ValueError(msg)
    return (
        f"Still more than one agent's work: {assessment.condition} is "
        f"{assessment.observed} against a limit of {assessment.limit}, and "
        f"{backstop}."
    )


__all__ = [
    "DEPTH_BACKSTOP",
    "MAX_SATISFIED_CRITERIA",
    "PLANNER_DECLINED",
    "SESSIONS_BACKSTOP",
    "SESSION_CEILING_BACKSTOP",
    "AtomicityAssessment",
    "AtomicityVerdict",
    "SubtaskAtomicityPolicy",
    "unsplit_reason",
]
