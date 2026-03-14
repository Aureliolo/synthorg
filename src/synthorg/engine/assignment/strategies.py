"""Task assignment strategy implementations.

Concrete strategies — Manual, RoleBased, LoadBalanced,
CostOptimized, Hierarchical, Auction.  The module-level
``STRATEGY_MAP`` (Hierarchical excluded; it requires
explicit construction via ``build_strategy_map``) and the
``build_strategy_map`` factory live in ``registry.py``.
"""

from typing import TYPE_CHECKING, Final

from synthorg.core.enums import AgentStatus

if TYPE_CHECKING:
    from synthorg.communication.delegation.hierarchy import HierarchyResolver
    from synthorg.core.agent import AgentIdentity
    from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.engine.assignment.models import (
    AssignmentCandidate,
    AssignmentRequest,
    AssignmentResult,
)
from synthorg.engine.decomposition.models import SubtaskDefinition
from synthorg.engine.errors import NoEligibleAgentError, TaskAssignmentError
from synthorg.observability import get_logger
from synthorg.observability.events.task_assignment import (
    TASK_ASSIGNMENT_AGENT_SCORED,
    TASK_ASSIGNMENT_AUCTION_BID,
    TASK_ASSIGNMENT_AUCTION_WON,
    TASK_ASSIGNMENT_CAPABILITY_FALLBACK,
    TASK_ASSIGNMENT_COST_OPTIMIZED,
    TASK_ASSIGNMENT_DELEGATOR_RESOLVED,
    TASK_ASSIGNMENT_FAILED,
    TASK_ASSIGNMENT_HIERARCHICAL_DELEGATED,
    TASK_ASSIGNMENT_HIERARCHY_TRANSITIVE,
    TASK_ASSIGNMENT_MANUAL_VALIDATED,
    TASK_ASSIGNMENT_NO_ELIGIBLE,
    TASK_ASSIGNMENT_WORKLOAD_BALANCED,
)

logger = get_logger(__name__)

STRATEGY_NAME_MANUAL: Final[str] = "manual"
STRATEGY_NAME_ROLE_BASED: Final[str] = "role_based"
STRATEGY_NAME_LOAD_BALANCED: Final[str] = "load_balanced"
STRATEGY_NAME_COST_OPTIMIZED: Final[str] = "cost_optimized"
STRATEGY_NAME_HIERARCHICAL: Final[str] = "hierarchical"
STRATEGY_NAME_AUCTION: Final[str] = "auction"


def _build_subtask_definition(request: AssignmentRequest) -> SubtaskDefinition:
    """Build a SubtaskDefinition adapter from an AssignmentRequest.

    Maps task-level fields (id, title, description, estimated_complexity)
    from the request's task and scoring hints (required_skills,
    required_role) from the request itself into a ``SubtaskDefinition``.

    Args:
        request: The assignment request.

    Returns:
        A SubtaskDefinition for scoring purposes.
    """
    return SubtaskDefinition(
        id=request.task.id,
        title=request.task.title,
        description=request.task.description,
        estimated_complexity=request.task.estimated_complexity,
        required_skills=request.required_skills,
        required_role=request.required_role,
    )


def _score_and_filter_candidates(
    scorer: AgentTaskScorer,
    request: AssignmentRequest,
    subtask: SubtaskDefinition,
) -> list[AssignmentCandidate]:
    """Score all agents and return filtered, sorted candidates.

    Shared scoring logic used by all scorer-based strategies.
    Filters out agents with non-ACTIVE status and agents at
    capacity (when ``max_concurrent_tasks`` and workload data
    are available) before scoring. Agents not present in the
    workload data are assumed to have zero active tasks and
    will not be filtered for capacity.

    Args:
        scorer: The agent-task scorer to use.
        request: The assignment request.
        subtask: The subtask definition for scoring.

    Returns:
        Sorted list of candidates whose score meets or exceeds
        ``request.min_score``, ordered by score descending.
    """
    # Build workload lookup for capacity filtering
    workload_map: dict[str, int] | None = None
    if request.max_concurrent_tasks is not None and request.workloads:
        workload_map = {w.agent_id: w.active_task_count for w in request.workloads}

    candidates: list[AssignmentCandidate] = []
    for agent in request.available_agents:
        if agent.status != AgentStatus.ACTIVE:
            continue

        # Skip agents at capacity
        if workload_map is not None and request.max_concurrent_tasks is not None:
            agent_id_str = str(agent.id)
            if agent_id_str not in workload_map:
                logger.debug(
                    TASK_ASSIGNMENT_AGENT_SCORED,
                    task_id=request.task.id,
                    agent_name=agent.name,
                    score=0.0,
                    reason="missing_workload_data",
                )
            active = workload_map.get(agent_id_str, 0)
            if active >= request.max_concurrent_tasks:
                logger.debug(
                    TASK_ASSIGNMENT_AGENT_SCORED,
                    task_id=request.task.id,
                    agent_name=agent.name,
                    score=0.0,
                    reason="at_capacity",
                    active_tasks=active,
                    max_concurrent=request.max_concurrent_tasks,
                )
                continue

        routing_candidate = scorer.score(agent, subtask)

        logger.debug(
            TASK_ASSIGNMENT_AGENT_SCORED,
            task_id=request.task.id,
            agent_name=agent.name,
            score=routing_candidate.score,
        )

        if routing_candidate.score >= request.min_score:
            candidates.append(
                AssignmentCandidate(
                    agent_identity=routing_candidate.agent_identity,
                    score=routing_candidate.score,
                    matched_skills=routing_candidate.matched_skills,
                    reason=routing_candidate.reason,
                ),
            )

    return sorted(candidates, key=lambda c: c.score, reverse=True)


class ManualAssignmentStrategy:
    """Assigns a task to its pre-designated agent.

    Requires ``task.assigned_to`` to be set. Validates that
    the designated agent exists in the pool and is ACTIVE.
    """

    __slots__ = ()

    @property
    def name(self) -> str:
        """Strategy name identifier."""
        return STRATEGY_NAME_MANUAL

    def _find_designated_agent(
        self,
        request: AssignmentRequest,
    ) -> AgentIdentity:
        """Find and validate the designated agent in the pool.

        Args:
            request: The assignment request.

        Returns:
            The validated, ACTIVE designated agent.

        Raises:
            TaskAssignmentError: If ``task.assigned_to`` is None.
            NoEligibleAgentError: If the designated agent is not in
                the pool or is not ACTIVE.
        """
        task = request.task
        if task.assigned_to is None:
            msg = (
                f"Manual assignment requires task.assigned_to to be set "
                f"for task {task.id!r}"
            )
            logger.warning(
                TASK_ASSIGNMENT_FAILED,
                task_id=task.id,
                strategy=self.name,
                error=msg,
            )
            raise TaskAssignmentError(msg)

        # Compare agent ID string forms (assigned_to is a plain
        # string, agent.id is a UUID)
        agent: AgentIdentity | None = None
        for available in request.available_agents:
            if str(available.id) == task.assigned_to:
                agent = available
                break

        if agent is None:
            msg = (
                f"Designated agent {task.assigned_to!r} not found "
                f"in available agents for task {task.id!r}"
            )
            logger.warning(
                TASK_ASSIGNMENT_FAILED,
                task_id=task.id,
                strategy=self.name,
                designated_agent=task.assigned_to,
                error=msg,
            )
            raise NoEligibleAgentError(msg)

        if agent.status != AgentStatus.ACTIVE:
            msg = (
                f"Designated agent {agent.name!r} has status "
                f"{agent.status.value!r}, expected 'active' "
                f"for task {task.id!r}"
            )
            logger.warning(
                TASK_ASSIGNMENT_FAILED,
                task_id=task.id,
                strategy=self.name,
                agent_name=agent.name,
                agent_status=agent.status.value,
                error=msg,
            )
            raise NoEligibleAgentError(msg)

        return agent

    def assign(self, request: AssignmentRequest) -> AssignmentResult:
        """Assign to the agent specified by ``task.assigned_to``.

        Args:
            request: The assignment request.

        Returns:
            Assignment result with the designated agent.

        Raises:
            TaskAssignmentError: If ``task.assigned_to`` is None.
            NoEligibleAgentError: If the designated agent is not in
                the pool or is not ACTIVE.
        """
        agent = self._find_designated_agent(request)
        task = request.task

        candidate = AssignmentCandidate(
            agent_identity=agent,
            score=1.0,
            matched_skills=(),
            reason="Manually assigned",
        )

        logger.debug(
            TASK_ASSIGNMENT_MANUAL_VALIDATED,
            task_id=task.id,
            agent_name=agent.name,
        )

        return AssignmentResult(
            task_id=task.id,
            strategy_used=self.name,
            selected=candidate,
            reason=f"Manually assigned to {agent.name!r}",
        )


class RoleBasedAssignmentStrategy:
    """Assigns a task to the best-scoring agent by capability.

    Uses ``AgentTaskScorer`` to score all available agents and
    selects the highest-scoring one above the minimum threshold.
    """

    __slots__ = ("_scorer",)

    def __init__(self, scorer: AgentTaskScorer) -> None:
        self._scorer = scorer

    @property
    def name(self) -> str:
        """Strategy name identifier."""
        return STRATEGY_NAME_ROLE_BASED

    def assign(self, request: AssignmentRequest) -> AssignmentResult:
        """Score and rank agents, selecting the best match.

        Returns ``selected=None`` when no candidates meet threshold.

        Args:
            request: The assignment request.

        Returns:
            Assignment result with the best-scoring agent.
        """
        subtask = _build_subtask_definition(request)
        candidates = _score_and_filter_candidates(
            self._scorer,
            request,
            subtask,
        )

        if not candidates:
            logger.warning(
                TASK_ASSIGNMENT_NO_ELIGIBLE,
                task_id=request.task.id,
                strategy=self.name,
                agent_count=len(request.available_agents),
                min_score=request.min_score,
            )
            return AssignmentResult(
                task_id=request.task.id,
                strategy_used=self.name,
                reason=(
                    f"No agents scored above threshold "
                    f"{request.min_score} for task {request.task.id!r}"
                ),
            )

        selected = candidates[0]
        alternatives = tuple(candidates[1:])

        return AssignmentResult(
            task_id=request.task.id,
            strategy_used=self.name,
            selected=selected,
            alternatives=alternatives,
            reason=f"Best match: {selected.agent_identity.name!r} "
            f"(score={selected.score:.2f})",
        )


class LoadBalancedAssignmentStrategy:
    """Assigns a task to the least-loaded eligible agent.

    Scores agents like ``RoleBasedAssignmentStrategy``, then
    sorts by workload (ascending) with score as tiebreaker
    (descending). Falls back to score-based ranking when
    workload data is absent or incomplete.
    """

    __slots__ = ("_scorer",)

    def __init__(self, scorer: AgentTaskScorer) -> None:
        self._scorer = scorer

    @property
    def name(self) -> str:
        """Strategy name identifier."""
        return STRATEGY_NAME_LOAD_BALANCED

    def assign(self, request: AssignmentRequest) -> AssignmentResult:
        """Score, filter by workload, and select the least-loaded agent.

        Returns ``selected=None`` when no candidates meet threshold.

        Args:
            request: The assignment request.

        Returns:
            Assignment result with the least-loaded eligible agent.
        """
        subtask = _build_subtask_definition(request)
        candidates = _score_and_filter_candidates(
            self._scorer,
            request,
            subtask,
        )

        if not candidates:
            logger.warning(
                TASK_ASSIGNMENT_NO_ELIGIBLE,
                task_id=request.task.id,
                strategy=self.name,
                agent_count=len(request.available_agents),
                min_score=request.min_score,
            )
            return AssignmentResult(
                task_id=request.task.id,
                strategy_used=self.name,
                reason=(
                    f"No agents scored above threshold "
                    f"{request.min_score} for task {request.task.id!r}"
                ),
            )

        workload_map: dict[str, int] = {
            w.agent_id: w.active_task_count for w in request.workloads
        }
        candidate_ids = {str(c.agent_identity.id) for c in candidates}
        has_complete_data = bool(workload_map) and candidate_ids <= workload_map.keys()

        if has_complete_data:
            candidates = sorted(
                candidates,
                key=lambda c: (
                    workload_map[str(c.agent_identity.id)],
                    -c.score,
                ),
            )
            logger.debug(
                TASK_ASSIGNMENT_WORKLOAD_BALANCED,
                task_id=request.task.id,
                agent_name=candidates[0].agent_identity.name,
                workload=workload_map[str(candidates[0].agent_identity.id)],
            )
        else:
            logger.warning(
                TASK_ASSIGNMENT_CAPABILITY_FALLBACK,
                task_id=request.task.id,
                strategy=self.name,
                partial_data=bool(workload_map),
            )

        selected = candidates[0]
        alternatives = tuple(candidates[1:])

        reason = (
            f"Least loaded: {selected.agent_identity.name!r} "
            f"(score={selected.score:.2f})"
            if has_complete_data
            else f"Best match (insufficient workload data): "
            f"{selected.agent_identity.name!r} "
            f"(score={selected.score:.2f})"
        )

        return AssignmentResult(
            task_id=request.task.id,
            strategy_used=self.name,
            selected=selected,
            alternatives=alternatives,
            reason=reason,
        )


class CostOptimizedAssignmentStrategy:
    """Assigns a task to the cheapest eligible agent.

    Scores agents like ``RoleBasedAssignmentStrategy``, then
    sorts by ``total_cost_usd`` (ascending) with score as
    tiebreaker (descending).  Falls back to score-based
    ranking when cost data is absent or incomplete.
    """

    __slots__ = ("_scorer",)

    def __init__(self, scorer: AgentTaskScorer) -> None:
        self._scorer = scorer

    @property
    def name(self) -> str:
        """Strategy name identifier."""
        return STRATEGY_NAME_COST_OPTIMIZED

    def assign(self, request: AssignmentRequest) -> AssignmentResult:
        """Score, sort by cost, and select the cheapest eligible agent.

        Returns ``selected=None`` when no candidates meet threshold.

        Args:
            request: The assignment request.

        Returns:
            Assignment result with the cheapest eligible agent.
        """
        subtask = _build_subtask_definition(request)
        candidates = _score_and_filter_candidates(
            self._scorer,
            request,
            subtask,
        )

        if not candidates:
            logger.warning(
                TASK_ASSIGNMENT_NO_ELIGIBLE,
                task_id=request.task.id,
                strategy=self.name,
                agent_count=len(request.available_agents),
                min_score=request.min_score,
            )
            return AssignmentResult(
                task_id=request.task.id,
                strategy_used=self.name,
                reason=(
                    f"No agents scored above threshold "
                    f"{request.min_score} for task {request.task.id!r}"
                ),
            )

        cost_map: dict[str, float] = {
            w.agent_id: w.total_cost_usd for w in request.workloads
        }
        candidate_ids = {str(c.agent_identity.id) for c in candidates}
        has_complete_data = bool(cost_map) and candidate_ids <= cost_map.keys()

        if has_complete_data:
            candidates = sorted(
                candidates,
                key=lambda c: (
                    cost_map[str(c.agent_identity.id)],
                    -c.score,
                ),
            )
            logger.debug(
                TASK_ASSIGNMENT_COST_OPTIMIZED,
                task_id=request.task.id,
                agent_name=candidates[0].agent_identity.name,
                total_cost_usd=cost_map[str(candidates[0].agent_identity.id)],
            )
        else:
            logger.warning(
                TASK_ASSIGNMENT_CAPABILITY_FALLBACK,
                task_id=request.task.id,
                strategy=self.name,
                partial_data=bool(cost_map),
            )

        selected = candidates[0]
        alternatives = tuple(candidates[1:])

        reason = (
            f"Cheapest: {selected.agent_identity.name!r} (score={selected.score:.2f})"
            if has_complete_data
            else f"Best match (insufficient cost data): "
            f"{selected.agent_identity.name!r} "
            f"(score={selected.score:.2f})"
        )

        return AssignmentResult(
            task_id=request.task.id,
            strategy_used=self.name,
            selected=selected,
            alternatives=alternatives,
            reason=reason,
        )


class HierarchicalAssignmentStrategy:
    """Assigns a task to a subordinate of the delegator.

    Identifies the delegator from ``task.delegation_chain[-1]``
    (the deepest in the root-first chain) or ``task.created_by`` as
    fallback, then filters the agent pool to the delegator's
    direct reports.  Falls back to transitive subordinates if
    no direct report matches.
    """

    __slots__ = ("_hierarchy", "_scorer")

    def __init__(
        self,
        scorer: AgentTaskScorer,
        hierarchy: HierarchyResolver,
    ) -> None:
        self._scorer = scorer
        self._hierarchy = hierarchy

    @property
    def name(self) -> str:
        """Strategy name identifier."""
        return STRATEGY_NAME_HIERARCHICAL

    def _resolve_delegator(self, request: AssignmentRequest) -> str:
        """Determine the delegator from the task.

        Uses ``delegation_chain[-1]`` if non-empty, else ``created_by``.

        Args:
            request: The assignment request.

        Returns:
            Delegator agent name.
        """
        task = request.task
        if task.delegation_chain:
            delegator = task.delegation_chain[-1]
            logger.debug(
                TASK_ASSIGNMENT_DELEGATOR_RESOLVED,
                task_id=task.id,
                delegator=delegator,
                source="delegation_chain",
            )
            return delegator
        logger.debug(
            TASK_ASSIGNMENT_DELEGATOR_RESOLVED,
            task_id=task.id,
            delegator=task.created_by,
            source="created_by",
        )
        return task.created_by

    def _filter_by_hierarchy(
        self,
        request: AssignmentRequest,
        delegator: str,
    ) -> tuple[AgentIdentity, ...]:
        """Filter available agents to subordinates of the delegator.

        Tries direct reports first, then transitive subordinates.

        Args:
            request: The assignment request.
            delegator: Delegator agent name.

        Returns:
            Filtered tuple of agents that are subordinates.
        """
        direct_reports = set(self._hierarchy.get_direct_reports(delegator))

        # Try direct reports first
        direct = tuple(a for a in request.available_agents if a.name in direct_reports)
        if direct:
            return direct

        # Fall back to transitive subordinates
        available_names = tuple(a.name for a in request.available_agents)
        logger.debug(
            TASK_ASSIGNMENT_HIERARCHY_TRANSITIVE,
            delegator=delegator,
            direct_reports=tuple(sorted(direct_reports)),
            available_agents=available_names,
        )
        return tuple(
            a
            for a in request.available_agents
            if self._hierarchy.is_subordinate(delegator, a.name)
        )

    def _is_known_delegator(self, delegator: str) -> bool:
        """Check if the delegator exists in the hierarchy.

        An agent is "known" if it has direct reports or a supervisor.

        Args:
            delegator: Delegator agent name.

        Returns:
            True if the delegator is part of the hierarchy.
        """
        has_reports = bool(self._hierarchy.get_direct_reports(delegator))
        has_supervisor = self._hierarchy.get_supervisor(delegator) is not None
        return has_reports or has_supervisor

    def _score_subordinates(
        self,
        request: AssignmentRequest,
        delegator: str,
        subordinates: tuple[AgentIdentity, ...],
    ) -> AssignmentResult:
        """Score subordinates and select the best match.

        Args:
            request: The original assignment request.
            delegator: Delegator agent name.
            subordinates: Filtered subordinate agents.

        Returns:
            Assignment result with best-scoring subordinate.
        """
        filtered_request = AssignmentRequest(
            task=request.task,
            available_agents=subordinates,
            workloads=request.workloads,
            min_score=request.min_score,
            required_skills=request.required_skills,
            required_role=request.required_role,
            max_concurrent_tasks=request.max_concurrent_tasks,
        )

        subtask = _build_subtask_definition(filtered_request)
        candidates = _score_and_filter_candidates(
            self._scorer,
            filtered_request,
            subtask,
        )

        if not candidates:
            logger.warning(
                TASK_ASSIGNMENT_NO_ELIGIBLE,
                task_id=request.task.id,
                strategy=self.name,
                delegator=delegator,
                agent_count=len(subordinates),
                min_score=request.min_score,
            )
            return AssignmentResult(
                task_id=request.task.id,
                strategy_used=self.name,
                reason=(
                    f"No subordinates of {delegator!r} scored above "
                    f"threshold {request.min_score} "
                    f"for task {request.task.id!r}"
                ),
            )

        selected = candidates[0]
        alternatives = tuple(candidates[1:])

        logger.debug(
            TASK_ASSIGNMENT_HIERARCHICAL_DELEGATED,
            task_id=request.task.id,
            delegator=delegator,
            agent_name=selected.agent_identity.name,
            score=selected.score,
        )

        return AssignmentResult(
            task_id=request.task.id,
            strategy_used=self.name,
            selected=selected,
            alternatives=alternatives,
            reason=f"Delegated from {delegator!r} to "
            f"{selected.agent_identity.name!r} "
            f"(score={selected.score:.2f})",
        )

    def assign(self, request: AssignmentRequest) -> AssignmentResult:
        """Assign to the best-scoring subordinate of the delegator.

        Returns ``selected=None`` when no candidates meet threshold.

        Args:
            request: The assignment request.

        Returns:
            Assignment result with the best subordinate.
        """
        delegator = self._resolve_delegator(request)

        if not self._is_known_delegator(delegator):
            logger.warning(
                TASK_ASSIGNMENT_NO_ELIGIBLE,
                task_id=request.task.id,
                strategy=self.name,
                delegator=delegator,
                reason="unknown_delegator",
            )
            return AssignmentResult(
                task_id=request.task.id,
                strategy_used=self.name,
                reason=f"Delegator {delegator!r} not found in hierarchy",
            )

        subordinates = self._filter_by_hierarchy(request, delegator)

        if not subordinates:
            logger.warning(
                TASK_ASSIGNMENT_NO_ELIGIBLE,
                task_id=request.task.id,
                strategy=self.name,
                delegator=delegator,
                reason="no_subordinates",
            )
            return AssignmentResult(
                task_id=request.task.id,
                strategy_used=self.name,
                reason=(
                    f"No subordinates of {delegator!r} found "
                    f"in available agents for task {request.task.id!r}"
                ),
            )

        return self._score_subordinates(request, delegator, subordinates)


class AuctionAssignmentStrategy:
    """Assigns a task via simulated auction bidding.

    Each agent's bid is ``capability_score * availability_factor``,
    where ``availability_factor = 1.0 / (1.0 + active_task_count)``.
    The highest bidder wins.  When no workload data is provided,
    all availability factors default to 1.0, making bids equal
    to raw capability scores.
    """

    __slots__ = ("_scorer",)

    def __init__(self, scorer: AgentTaskScorer) -> None:
        self._scorer = scorer

    @property
    def name(self) -> str:
        """Strategy name identifier."""
        return STRATEGY_NAME_AUCTION

    def assign(self, request: AssignmentRequest) -> AssignmentResult:
        """Run a simulated auction and select the highest bidder.

        Returns ``selected=None`` when no candidates meet threshold.

        Args:
            request: The assignment request.

        Returns:
            Assignment result with the highest-bidding agent.
        """
        subtask = _build_subtask_definition(request)
        candidates = _score_and_filter_candidates(
            self._scorer,
            request,
            subtask,
        )

        if not candidates:
            logger.warning(
                TASK_ASSIGNMENT_NO_ELIGIBLE,
                task_id=request.task.id,
                strategy=self.name,
                agent_count=len(request.available_agents),
                min_score=request.min_score,
            )
            return AssignmentResult(
                task_id=request.task.id,
                strategy_used=self.name,
                reason=(
                    f"No agents scored above threshold "
                    f"{request.min_score} for task {request.task.id!r}"
                ),
            )

        workload_map: dict[str, int] = {
            w.agent_id: w.active_task_count for w in request.workloads
        }
        candidate_ids = {str(c.agent_identity.id) for c in candidates}
        has_complete_data = bool(workload_map) and candidate_ids <= workload_map.keys()

        if not has_complete_data and workload_map:
            logger.warning(
                TASK_ASSIGNMENT_CAPABILITY_FALLBACK,
                task_id=request.task.id,
                strategy=self.name,
                partial_data=True,
            )

        # Compute bids: score * availability_factor
        bids: list[tuple[AssignmentCandidate, float]] = []
        for candidate in candidates:
            if has_complete_data:
                active_tasks = workload_map[str(candidate.agent_identity.id)]
                availability = 1.0 / (1.0 + active_tasks)
            else:
                availability = 1.0
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

        # Sort by bid descending, then score descending as tiebreaker
        bids.sort(key=lambda x: (x[1], x[0].score), reverse=True)

        selected = bids[0][0]
        alternatives = tuple(b[0] for b in bids[1:])

        logger.debug(
            TASK_ASSIGNMENT_AUCTION_WON,
            task_id=request.task.id,
            agent_name=selected.agent_identity.name,
            winning_bid=bids[0][1],
        )

        return AssignmentResult(
            task_id=request.task.id,
            strategy_used=self.name,
            selected=selected,
            alternatives=alternatives,
            reason=f"Auction winner: {selected.agent_identity.name!r} "
            f"(bid={bids[0][1]:.4f})",
        )
