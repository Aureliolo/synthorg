"""Concrete ``CandidateRanker`` implementations.

Each ranker takes the score-descending list produced by
``score_and_filter_candidates`` and returns a ``RankingResult``
per the strategy's ranking rule.
"""

from collections.abc import Sequence
from typing import Final

from synthorg.engine.assignment.models import (
    AssignmentCandidate,
    AssignmentRequest,
)
from synthorg.engine.assignment.ranker_protocol import RankingResult
from synthorg.observability import get_logger
from synthorg.observability.events.task_assignment import (
    TASK_ASSIGNMENT_AUCTION_BID,
    TASK_ASSIGNMENT_AUCTION_WON,
    TASK_ASSIGNMENT_CAPABILITY_FALLBACK,
    TASK_ASSIGNMENT_COST_OPTIMIZED,
    TASK_ASSIGNMENT_WORKLOAD_BALANCED,
)

logger = get_logger(__name__)

RANKER_NAME_SCORE_DESCENDING: Final[str] = "score_descending"
RANKER_NAME_WORKLOAD_ASCENDING: Final[str] = "workload_ascending"
RANKER_NAME_COST_DESCENDING: Final[str] = "cost_descending"
RANKER_NAME_AUCTION_BID: Final[str] = "auction_bid"


def _candidates_covered_by(
    candidates: Sequence[AssignmentCandidate],
    keys: set[str],
) -> bool:
    """True if every candidate's id is in ``keys``.

    Returns:
        ``True`` when every candidate's ``str(agent_identity.id)``
        appears in ``keys``; ``False`` otherwise.
    """
    return all(str(c.agent_identity.id) in keys for c in candidates)


def _score_ordered_alternatives(
    candidates: Sequence[AssignmentCandidate],
    *,
    selected_agent_id: str,
) -> tuple[AssignmentCandidate, ...]:
    """Return non-selected candidates in the input's score-descending order.

    The input ``candidates`` is the score-descending output of
    ``score_and_filter_candidates`` (per the ranker contract). By
    iterating it directly and skipping the selected agent, we
    preserve the original score-order tie-break for equal-score
    candidates instead of letting each ranker's intermediate
    ordering leak through ``sorted(..., key=score)``'s stability.
    Used by the workload, cost, and auction rankers so their
    alternatives lists are byte-for-byte identical when scores tie.

    Args:
        candidates: The score-descending candidate list.
        selected_agent_id: ``str(agent_identity.id)`` of the picked
            candidate; that agent is excluded from the result.

    Returns:
        Tuple of non-selected candidates in score-descending order.
    """
    return tuple(c for c in candidates if str(c.agent_identity.id) != selected_agent_id)


class ScoreDescendingRanker:
    """Selects the highest-scoring candidate.

    The input is already sorted by score descending, so this ranker
    is a thin pass-through: ``candidates[0]`` is the winner and
    ``candidates[1:]`` are the alternatives in score-descending
    order.

    Used by the role-based and (as a tail step) hierarchical
    strategies.
    """

    __slots__ = ()

    @property
    def name(self) -> str:
        """Ranker identifier."""
        return RANKER_NAME_SCORE_DESCENDING

    def rank(
        self,
        candidates: Sequence[AssignmentCandidate],
        request: AssignmentRequest,
    ) -> RankingResult:
        """Pick the highest scorer; alternatives stay in score order.

        Args:
            candidates: Score-descending candidate list.
            request: Original assignment request (unused; this ranker
                has no secondary key).

        Returns:
            ``RankingResult`` whose ``selected`` is ``candidates[0]``
            and whose ``alternatives`` is the tail in score order.
        """
        del request
        selected = candidates[0]
        return RankingResult(
            selected=selected,
            alternatives=tuple(candidates[1:]),
            reason=(
                f"Best match: {selected.agent_identity.name!r} "
                f"(score={selected.score:.2f})"
            ),
        )


class WorkloadAscendingRanker:
    """Selects the candidate with the lowest active task count.

    Sorts by ``(active_task_count, -score)`` so equal-workload
    agents tie-break by score. Falls back to score-only ordering
    when workload data is missing or does not cover every
    candidate (logs ``TASK_ASSIGNMENT_CAPABILITY_FALLBACK``).
    Alternatives are score-ranked (not workload-ranked) so callers
    that treat them as a generic fallback list see a consistent
    ordering -- preserves the quirk from the prior
    ``LoadBalancedAssignmentStrategy``.
    """

    __slots__ = ()

    @property
    def name(self) -> str:
        """Ranker identifier."""
        return RANKER_NAME_WORKLOAD_ASCENDING

    def rank(
        self,
        candidates: Sequence[AssignmentCandidate],
        request: AssignmentRequest,
    ) -> RankingResult:
        """Pick the least-loaded candidate.

        Args:
            candidates: Score-descending candidate list.
            request: Original assignment request (read for
                ``workloads``).

        Returns:
            ``RankingResult`` ranking by ``(workload, -score)``.
            Alternatives are returned in the input's score-descending
            order (via ``_score_ordered_alternatives``) for
            cross-ranker consistency.
        """
        workload_map: dict[str, int] = {
            w.agent_id: w.active_task_count for w in request.workloads
        }
        has_complete_data = bool(workload_map) and _candidates_covered_by(
            candidates,
            set(workload_map.keys()),
        )
        if has_complete_data:
            ranked = sorted(
                candidates,
                key=lambda c: (
                    workload_map[str(c.agent_identity.id)],
                    -c.score,
                ),
            )
            selected = ranked[0]
            logger.debug(
                TASK_ASSIGNMENT_WORKLOAD_BALANCED,
                task_id=request.task.id,
                agent_name=selected.agent_identity.name,
                workload=workload_map[str(selected.agent_identity.id)],
            )
            alternatives = _score_ordered_alternatives(
                candidates,
                selected_agent_id=str(selected.agent_identity.id),
            )
            return RankingResult(
                selected=selected,
                alternatives=alternatives,
                reason=(
                    f"Least loaded: {selected.agent_identity.name!r} "
                    f"(score={selected.score:.2f})"
                ),
            )
        logger.warning(
            TASK_ASSIGNMENT_CAPABILITY_FALLBACK,
            task_id=request.task.id,
            strategy=self.name,
            partial_data=bool(workload_map),
        )
        selected = candidates[0]
        return RankingResult(
            selected=selected,
            alternatives=tuple(candidates[1:]),
            reason=(
                f"Best match (insufficient workload data): "
                f"{selected.agent_identity.name!r} "
                f"(score={selected.score:.2f})"
            ),
        )


class CostDescendingRanker:
    """Selects the cheapest candidate (lowest ``total_cost``).

    The class name reads "descending" but the behaviour is
    "ascending cost" (lowest cost wins). Sorts by ``(total_cost,
    -score)`` so equal-cost agents tie-break by score. Falls back
    to score-only ordering when cost data is missing or does not
    cover every candidate (logs
    ``TASK_ASSIGNMENT_CAPABILITY_FALLBACK``).
    """

    __slots__ = ()

    @property
    def name(self) -> str:
        """Ranker identifier."""
        return RANKER_NAME_COST_DESCENDING

    def rank(
        self,
        candidates: Sequence[AssignmentCandidate],
        request: AssignmentRequest,
    ) -> RankingResult:
        """Pick the cheapest candidate.

        Args:
            candidates: Score-descending candidate list.
            request: Original assignment request (read for
                ``workloads`` cost data).

        Returns:
            ``RankingResult`` ranking by ``(total_cost, -score)``
            (lowest cost wins; score breaks cost ties).
            Alternatives are returned in the input's score-descending
            order (via ``_score_ordered_alternatives``) for
            cross-ranker consistency.
        """
        cost_map: dict[str, float] = {
            w.agent_id: w.total_cost for w in request.workloads
        }
        has_complete_data = bool(cost_map) and _candidates_covered_by(
            candidates,
            set(cost_map.keys()),
        )
        if has_complete_data:
            ranked = sorted(
                candidates,
                key=lambda c: (
                    cost_map[str(c.agent_identity.id)],
                    -c.score,
                ),
            )
            selected = ranked[0]
            logger.debug(
                TASK_ASSIGNMENT_COST_OPTIMIZED,
                task_id=request.task.id,
                agent_name=selected.agent_identity.name,
                total_cost=cost_map[str(selected.agent_identity.id)],
            )
            alternatives = _score_ordered_alternatives(
                candidates,
                selected_agent_id=str(selected.agent_identity.id),
            )
            return RankingResult(
                selected=selected,
                alternatives=alternatives,
                reason=(
                    f"Cheapest: {selected.agent_identity.name!r} "
                    f"(score={selected.score:.2f})"
                ),
            )
        logger.warning(
            TASK_ASSIGNMENT_CAPABILITY_FALLBACK,
            task_id=request.task.id,
            strategy=self.name,
            partial_data=bool(cost_map),
        )
        selected = candidates[0]
        return RankingResult(
            selected=selected,
            alternatives=tuple(candidates[1:]),
            reason=(
                f"Best match (insufficient cost data): "
                f"{selected.agent_identity.name!r} "
                f"(score={selected.score:.2f})"
            ),
        )


class AuctionBidRanker:
    """Selects the highest auction bidder.

    Each agent's bid is ``score * (1.0 / (1.0 + active_task_count))``.
    Higher score with lower workload yields a higher bid; ties on bid
    fall back to score. When no workload data is provided, all
    availability factors default to 1.0 (so bids equal the raw
    capability scores) -- preserves the prior auction quirk where
    ``empty workloads == role-based``. Alternatives are score-ranked
    (not bid-ranked) for caller-side consistency.
    """

    __slots__ = ()

    @property
    def name(self) -> str:
        """Ranker identifier."""
        return RANKER_NAME_AUCTION_BID

    def rank(
        self,
        candidates: Sequence[AssignmentCandidate],
        request: AssignmentRequest,
    ) -> RankingResult:
        """Pick the highest-bidding candidate.

        Args:
            candidates: Score-descending candidate list.
            request: Original assignment request (read for
                ``workloads`` to compute availability factors).

        Returns:
            ``RankingResult`` ranking by ``(bid, score)`` desc where
            ``bid = score * 1/(1 + active_task_count)``.
            Alternatives are returned in the input's score-descending
            order (via ``_score_ordered_alternatives``) for
            cross-ranker consistency.
        """
        workload_map: dict[str, int] = {
            w.agent_id: w.active_task_count for w in request.workloads
        }
        has_complete_data = bool(workload_map) and _candidates_covered_by(
            candidates,
            set(workload_map.keys()),
        )
        if not has_complete_data and workload_map:
            logger.warning(
                TASK_ASSIGNMENT_CAPABILITY_FALLBACK,
                task_id=request.task.id,
                strategy=self.name,
                partial_data=True,
            )
        bids: list[tuple[AssignmentCandidate, float]] = []
        for candidate in candidates:
            availability = (
                1.0 / (1.0 + workload_map[str(candidate.agent_identity.id)])
                if has_complete_data
                else 1.0
            )
            bid = candidate.score * availability
            logger.debug(
                TASK_ASSIGNMENT_AUCTION_BID,
                task_id=request.task.id,
                agent_name=candidate.agent_identity.name,
                score=candidate.score,
                availability=availability,
                bid=bid,
            )
            bids.append((candidate, bid))
        ranked_bids = sorted(
            bids,
            key=lambda item: (item[1], item[0].score),
            reverse=True,
        )
        selected, winning_bid = ranked_bids[0]
        alternatives = _score_ordered_alternatives(
            candidates,
            selected_agent_id=str(selected.agent_identity.id),
        )
        logger.debug(
            TASK_ASSIGNMENT_AUCTION_WON,
            task_id=request.task.id,
            agent_name=selected.agent_identity.name,
            winning_bid=winning_bid,
        )
        return RankingResult(
            selected=selected,
            alternatives=alternatives,
            reason=(
                f"Auction winner: {selected.agent_identity.name!r} "
                f"(bid={winning_bid:.4f})"
            ),
        )
