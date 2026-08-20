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

The sibling question at the objective level is already answered by
:class:`~synthorg.engine.pipeline.policy.threshold.LeafThresholdRoutingPolicy`,
which reads the same artefact count. This asks it per subtask, at the seam that
can act on the answer by decomposing again.
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
            ``coordination.leaf_subtask_threshold``.
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


__all__ = [
    "MAX_SATISFIED_CRITERIA",
    "AtomicityAssessment",
    "AtomicityVerdict",
    "SubtaskAtomicityPolicy",
]
