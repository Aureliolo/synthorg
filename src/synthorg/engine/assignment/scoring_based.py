"""Composite scoring-based task assignment strategy.

One class, ``ScoringBasedAssignmentStrategy``, composes a
``(pool_filter, ranker)`` pair with the single shared
``AgentTaskScorer`` to produce every scoring strategy the registry
offers: role-based, load-balanced, cost-optimized, hierarchical and
auction. Scoring is identical across all of them, so the axes that
actually diverge are pool filtering and ranking, and those are what
the composition varies.
"""

from collections.abc import Callable

from synthorg.core.agent import AgentIdentity
from synthorg.core.capability_fit import partition_by_fit
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.assignment._shared import (
    build_subtask_definition,
    resolve_low_confidence_outcome,
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
from synthorg.engine.routing_policy.capability_policy import (
    CapabilityPolicy,
    rank_of,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.task_assignment import (
    TASK_ASSIGNMENT_BELOW_CAPABILITY_FLOOR,
    TASK_ASSIGNMENT_LOW_CONFIDENCE,
    TASK_ASSIGNMENT_NO_ELIGIBLE,
    TASK_ASSIGNMENT_REASON_REWRITER_FAILED,
    TASK_ASSIGNMENT_UNDER_CAPABILITY,
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

    __slots__ = ("_capability", "_name", "_pool_filter", "_ranker", "_scorer")

    def __init__(
        self,
        *,
        name: str,
        scorer: AgentTaskScorer,
        pool_filter: CandidatePoolFilter,
        ranker: CandidateRanker,
        capability: CapabilityPolicy | None = None,
    ) -> None:
        self._name = name
        self._scorer = scorer
        self._pool_filter = pool_filter
        self._ranker = ranker
        self._capability = capability

    @property
    def name(self) -> str:
        """Strategy name identifier (e.g. ``role_based``)."""
        return self._name

    def assign(self, request: AssignmentRequest) -> AssignmentResult:
        """Run the filter -> capability ladder -> score -> rank pipeline.

        Returns ``selected=None`` when the pool is empty after filtering
        (the ``pool_filter`` narrows to nothing, or no agent is active and
        below capacity), when no agent may take the work at its capability
        requirement, or when the subtask carries a hard requirement (a
        ``required_role`` / ``required_skills``) that no agent satisfies --
        an unstaffable requirement surfaces as no-eligible rather than
        drawing an unqualified agent. A subtask with *no* requirement is
        staffable by anyone: the best available agent is assigned and
        flagged ``low_confidence`` so the organisation never deadlocks on
        an unconstrained task.

        The capability ladder runs ABOVE the scoring rather than as a score
        input. An agent whose model cannot carry the work does not become
        able to by fitting the role well, and preferring an exact rung over
        a stronger one is the org's standing cost discipline, applied on
        every assignment. The ranker still decides within whichever band
        answers, so the score / workload / cost / auction axis is untouched
        and cost buys the cheapest agent AT that rung rather than the
        cheapest that could scrape through.

        Returns:
            The :class:`AssignmentResult` carrying the selected
            candidate, alternatives, and a human-readable reason; or
            a no-eligible result with structured reason when no agent
            could be selected.
        """
        filter_result = self._pool_filter.filter(request)
        if not filter_result.agents:
            return self._empty_pool_result(request, filter_result.reason)

        capable = self._best_fitting_band(request, filter_result.agents)
        if not capable:
            return self._below_floor_result(request, len(filter_result.agents))

        effective_request = self._effective_request(request, capable)
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
        if low_confidence:
            # The best available agent is a marginal fit. Assign it anyway (a
            # marginal-fit agent can still do the work, unlike a sub-tier model
            # that cannot tool-call) so the organisation never deadlocks, but
            # flag it: high/critical work is logged as an operator-facing
            # escalation and the low_confidence flag surfaces on the API /
            # dashboard for review. The stakes-gated red-team review gate
            # re-reviews consequential output downstream.
            self._log_low_confidence(
                effective_request,
                ranking.selected,
                outcome=resolve_low_confidence_outcome(effective_request.stakes),
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

    def _log_low_confidence(
        self,
        request: AssignmentRequest,
        selected: AssignmentCandidate,
        *,
        outcome: str,
    ) -> None:
        """Emit the low-confidence marginal-fit warning (WARNING level).

        For high/critical stakes this is the operator-facing escalation signal:
        the assignment still proceeds, but the WARNING plus the ``low_confidence``
        flag on the result make the marginal fit visible for review.
        """
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

    def _best_fitting_band(
        self,
        request: AssignmentRequest,
        agents: tuple[AgentIdentity, ...],
    ) -> tuple[AgentIdentity, ...]:
        """Narrow *agents* to the band that best fits the requirement.

        The ladder: agents at the exact rung the work demands, else the
        nearest rung above, else (where the stakes allow it) the nearest rung
        below, with the concession logged. An agent the stakes forbid is
        dropped before the ladder runs, so a critical task never lands on a
        weaker agent by way of an empty upper band.

        Returns:
            The surviving agents, or *agents* unchanged when the request
            carries no requirement or no policy is wired to judge one.
        """
        required = request.required_capability
        if required is None or self._capability is None:
            return agents
        policy = self._capability
        sanctioned = tuple(
            agent
            for agent in agents
            if policy.judge(
                model=agent.model,
                stakes=request.stakes,
                complexity=request.task.estimated_complexity,
            ).sanctioned
        )
        banded = partition_by_fit(
            sanctioned,
            lambda agent: rank_of(policy.capability_of(agent.model)),
            rank_of(required),
        )
        if banded is None:
            return ()
        band, fit = banded
        if fit == "lower":
            logger.warning(
                TASK_ASSIGNMENT_UNDER_CAPABILITY,
                task_id=str(request.task.id),
                strategy=self.name,
                stakes=request.stakes.value,
                required_capability=required,
                band_capability=policy.capability_of(band[0].model),
                candidates=len(band),
                note=(
                    "No agent runs at or above the rung this work demands; "
                    "assigned to the strongest available agent instead."
                ),
            )
        return band

    def _below_floor_result(
        self,
        request: AssignmentRequest,
        considered: int,
    ) -> AssignmentResult:
        """Build a no-eligible result when no agent clears the floor.

        The organisation's answer to this is an agent at the needed rung, not
        a stronger model behind an existing agent's name, so the reason names
        the rung the operator has to staff.

        Returns:
            An :class:`AssignmentResult` with ``selected=None``.
        """
        required = request.required_capability
        logger.warning(
            TASK_ASSIGNMENT_BELOW_CAPABILITY_FLOOR,
            task_id=str(request.task.id),
            strategy=self.name,
            stakes=request.stakes.value,
            required_capability=required,
            agent_count=considered,
        )
        return AssignmentResult(
            task_id=str(request.task.id),
            strategy_used=self.name,
            reason=(
                f"No available agent runs a {required} model, which "
                f"{request.stakes.value}-stakes work requires"
            ),
        )

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
