"""Composite scoring-based task assignment strategy.

The single class ``ScoringBasedAssignmentStrategy`` replaces the
five role-based / load-balanced / cost-optimized / hierarchical /
auction strategies. Each former strategy now corresponds to a
particular ``(pool_filter, ranker)`` composition; the scorer is the
same shared ``AgentTaskScorer`` for all of them (the previous
"injected scorer" axis was the wrong axis -- the divergent axes
are pool filtering and ranking).
"""

from collections.abc import Callable

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task_enums import Stakes, compare_stakes
from synthorg.engine.assignment._shared import (
    build_subtask_definition,
    score_and_filter_candidates,
)
from synthorg.engine.assignment.models import (
    AssignmentCandidate,
    AssignmentRequest,
    AssignmentResult,
)
from synthorg.engine.assignment.pool_filter_protocol import CandidatePoolFilter
from synthorg.engine.assignment.ranker_protocol import CandidateRanker, RankingResult
from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.task_assignment import (
    TASK_ASSIGNMENT_LOW_CONFIDENCE,
    TASK_ASSIGNMENT_NO_ELIGIBLE,
    TASK_ASSIGNMENT_REASON_REWRITER_FAILED,
)

ReasonRewriter = Callable[[AssignmentCandidate], str]

logger = get_logger(__name__)


class ScoringBasedAssignmentStrategy:
    """Compose ``(pool_filter, scorer, ranker)`` into one strategy.

    ``pool_filter`` runs first to narrow ``request.available_agents``
    along an axis the scorer cannot express (e.g. hierarchical
    subordinates). ``scorer`` then scores the survivors via the
    shared ``score_and_filter_candidates`` helper. ``ranker`` orders
    the result and explains the choice.

    The strategy's ``name`` is injected so the registry can expose
    role-based / load-balanced / cost-optimized / hierarchical /
    auction strategies without subclassing.
    """

    __slots__ = ("_name", "_pool_filter", "_ranker", "_scorer")

    def __init__(
        self,
        *,
        name: str,
        scorer: AgentTaskScorer,
        pool_filter: CandidatePoolFilter,
        ranker: CandidateRanker,
    ) -> None:
        self._name = name
        self._scorer = scorer
        self._pool_filter = pool_filter
        self._ranker = ranker

    @property
    def name(self) -> str:
        """Strategy name identifier (e.g. ``role_based``)."""
        return self._name

    def assign(self, request: AssignmentRequest) -> AssignmentResult:
        """Run the filter -> score -> rank pipeline.

        Returns ``selected=None`` when either the filter narrows to
        an empty pool (with the filter's ``reason``) or when no
        survivor scores above ``request.min_score``.

        Returns:
            The :class:`AssignmentResult` carrying the selected
            candidate, alternatives, and a human-readable reason; or
            a no-eligible result with structured reason when no agent
            could be selected.
        """
        filter_result = self._pool_filter.filter(request)
        if not filter_result.agents:
            return self._empty_pool_result(request, filter_result.reason)

        effective_request = self._effective_request(request, filter_result.agents)
        subtask = build_subtask_definition(effective_request)
        candidates = score_and_filter_candidates(
            self._scorer,
            effective_request,
            subtask,
        )
        if not candidates:
            return self._no_eligible_result(effective_request)

        ranking = self._ranker.rank(candidates, effective_request)
        low_confidence = (
            ranking.selected.score < effective_request.effective_low_confidence_score
        )
        if low_confidence and self._is_high_stakes(effective_request.stakes):
            return self._below_confidence_result(effective_request, ranking.selected)
        if low_confidence:
            self._log_low_confidence(
                effective_request,
                ranking.selected,
                outcome="proceeded",
            )
        reason = self._compose_reason(
            request,
            filter_result.rewrite_success_reason,
            ranking,
        )
        return AssignmentResult(
            task_id=str(request.task.id),
            strategy_used=self.name,
            selected=ranking.selected,
            alternatives=ranking.alternatives,
            reason=reason,
            low_confidence=low_confidence,
        )

    @staticmethod
    def _is_high_stakes(stakes: Stakes) -> bool:
        """Whether *stakes* is at or above HIGH (a marginal fit is rejected).

        Returns:
            ``True`` when *stakes* is HIGH or CRITICAL.
        """
        return compare_stakes(stakes, Stakes.HIGH) >= 0

    def _log_low_confidence(
        self,
        request: AssignmentRequest,
        selected: AssignmentCandidate,
        *,
        outcome: str,
    ) -> None:
        """Emit the low-confidence marginal-fit warning."""
        logger.warning(
            TASK_ASSIGNMENT_LOW_CONFIDENCE,
            task_id=str(request.task.id),
            strategy=self.name,
            agent_id=str(selected.agent_identity.id),
            score=selected.score,
            threshold=request.effective_low_confidence_score,
            stakes=request.stakes.value,
            outcome=outcome,
        )

    def _below_confidence_result(
        self,
        request: AssignmentRequest,
        selected: AssignmentCandidate,
    ) -> AssignmentResult:
        """Reject a marginal fit for high/critical-stakes work.

        The best candidate cleared eligibility but scored below the
        low-confidence band, and the task's stakes are too high to proceed on a
        marginal fit, so no agent is selected and the caller escalates.

        Returns:
            An :class:`AssignmentResult` with ``selected=None`` and a reason
            citing the low-confidence rejection.
        """
        self._log_low_confidence(request, selected, outcome="rejected")
        return AssignmentResult(
            task_id=str(request.task.id),
            strategy_used=self.name,
            reason=(
                f"Best fit scored {selected.score:g} below the low-confidence "
                f"floor {request.effective_low_confidence_score:g} for "
                f"{request.stakes.value}-stakes task {str(request.task.id)!r}; "
                f"no agent is a confident fit"
            ),
        )

    def _compose_reason(
        self,
        request: AssignmentRequest,
        rewriter: ReasonRewriter | None,
        ranking: RankingResult,
    ) -> str:
        """Run the optional pool-filter reason rewriter, defensively.

        A buggy filter callable should not crash the entire assignment.
        On exception we log a warning and fall back to the ranker's
        reason -- the assignment itself is still valid since the
        rewriter only affects the human-readable explanation.

        Returns:
            The rewriter's output when it ran successfully; the
            ranker's reason when no rewriter is configured or when
            the rewriter raised.
        """
        if rewriter is None:
            return ranking.reason
        try:
            return rewriter(ranking.selected)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort side channel
            reraise_critical(exc)
            logger.warning(
                TASK_ASSIGNMENT_REASON_REWRITER_FAILED,
                task_id=str(request.task.id),
                strategy=self.name,
                pool_filter=self._pool_filter.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ranking.reason

    def _empty_pool_result(
        self,
        request: AssignmentRequest,
        filter_reason: str | None,
    ) -> AssignmentResult:
        """Build a no-eligible result when the pool filter returned empty.

        Returns:
            An :class:`AssignmentResult` with ``selected=None`` and
            either the filter's structured reason or a generic
            fallback reason.
        """
        reason = filter_reason or (
            f"Pool filter {self._pool_filter.name!r} returned no candidates"
        )
        return AssignmentResult(
            task_id=str(request.task.id),
            strategy_used=self.name,
            reason=reason,
        )

    def _no_eligible_result(self, request: AssignmentRequest) -> AssignmentResult:
        """Build a no-eligible result when no candidate passed the score threshold.

        Returns:
            An :class:`AssignmentResult` with ``selected=None`` and a
            reason citing the score threshold.
        """
        logger.warning(
            TASK_ASSIGNMENT_NO_ELIGIBLE,
            task_id=str(request.task.id),
            strategy=self.name,
            agent_count=len(request.available_agents),
            min_score=request.min_score,
        )
        return AssignmentResult(
            task_id=str(request.task.id),
            strategy_used=self.name,
            reason=(
                f"No agents scored above threshold "
                f"{request.min_score} for task {str(request.task.id)!r}"
            ),
        )

    @staticmethod
    def _effective_request(
        request: AssignmentRequest,
        narrowed_agents: tuple[AgentIdentity, ...],
    ) -> AssignmentRequest:
        """Return ``request`` if no narrowing occurred, else a narrowed copy.

        Uses Pydantic ``model_copy(update=...)`` so any new
        ``AssignmentRequest`` field automatically rides through
        without an extra edit here.
        """
        if narrowed_agents == request.available_agents:
            return request
        return request.model_copy(update={"available_agents": narrowed_agents})
