"""Candidate ranker protocol for task assignment.

Post-scoring ordering of candidates. Given a list of scored
``AssignmentCandidate`` instances and the original request (for
secondary keys like workload or cost), a ranker returns the
selected candidate plus the alternatives tuple plus the
human-readable reason that lands in ``AssignmentResult.reason``.

The ranker is the right axis of variation across all scoring-based
assignment strategies: each one filters then scores identically,
and differs only in how the resulting list is ordered.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from synthorg.engine.assignment.models import (
        AssignmentCandidate,
        AssignmentRequest,
    )


@dataclass(frozen=True, slots=True)
class RankingResult:
    """Output of ``CandidateRanker.rank()``.

    Attributes:
        selected: The chosen candidate.
        alternatives: Non-selected candidates in the ranker's
            chosen order.
        reason: Human-readable explanation for
            ``AssignmentResult.reason``.
    """

    selected: AssignmentCandidate
    alternatives: tuple[AssignmentCandidate, ...]
    reason: str

    def __post_init__(self) -> None:
        """Enforce that ``selected`` is not duplicated in ``alternatives``.

        Raises:
            ValueError: When the selected candidate's agent id appears
                in ``alternatives``.
        """
        selected_id = self.selected.agent_identity.id
        for alt in self.alternatives:
            if alt.agent_identity.id == selected_id:
                msg = (
                    f"RankingResult: selected candidate "
                    f"{self.selected.agent_identity.name!r} also appears in "
                    f"alternatives"
                )
                raise ValueError(msg)


@runtime_checkable
class CandidateRanker(Protocol):
    """Protocol for ordering scored candidates.

    Implementations receive a non-empty sequence of candidates
    sorted by score descending (the output of
    ``score_and_filter_candidates``) plus the request, and return
    a ``RankingResult``.

    Implementations choose both the selection key and the
    alternatives ordering. Some rankers (load-balanced, auction)
    use a primary key for selection but keep alternatives ordered
    by score for caller convenience.
    """

    @property
    def name(self) -> str:
        """Ranker identifier (used for logging and diagnostics)."""
        ...

    def rank(
        self,
        candidates: Sequence[AssignmentCandidate],
        request: AssignmentRequest,
    ) -> RankingResult:
        """Order scored candidates, pick a winner, and explain.

        Args:
            candidates: Non-empty sequence of scored candidates,
                already sorted by score descending.
            request: The original assignment request (for secondary
                keys like workload, cost, project context).

        Returns:
            A ``RankingResult`` with selected, alternatives, and
            a human-readable reason.
        """
        ...
