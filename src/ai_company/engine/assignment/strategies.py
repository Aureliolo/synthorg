"""Task assignment strategy implementations.

Three concrete strategies — Manual, RoleBased, LoadBalanced — plus
a module-level strategy registry mapping names to singletons.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from ai_company.core.enums import AgentStatus

if TYPE_CHECKING:
    from ai_company.core.agent import AgentIdentity
from ai_company.engine.assignment.models import (
    AssignmentCandidate,
    AssignmentRequest,
    AssignmentResult,
)
from ai_company.engine.decomposition.models import SubtaskDefinition
from ai_company.engine.errors import NoEligibleAgentError, TaskAssignmentError
from ai_company.engine.routing.scorer import AgentTaskScorer
from ai_company.observability import get_logger
from ai_company.observability.events.task_assignment import (
    TASK_ASSIGNMENT_AGENT_SCORED,
    TASK_ASSIGNMENT_FAILED,
    TASK_ASSIGNMENT_MANUAL_VALIDATED,
    TASK_ASSIGNMENT_NO_ELIGIBLE,
    TASK_ASSIGNMENT_WORKLOAD_BALANCED,
)

logger = get_logger(__name__)

STRATEGY_NAME_MANUAL: Final[str] = "manual"
STRATEGY_NAME_ROLE_BASED: Final[str] = "role_based"
STRATEGY_NAME_LOAD_BALANCED: Final[str] = "load_balanced"


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

    Shared scoring logic used by both ``RoleBasedAssignmentStrategy``
    and ``LoadBalancedAssignmentStrategy``.

    Args:
        scorer: The agent-task scorer to use.
        request: The assignment request.
        subtask: The subtask definition for scoring.

    Returns:
        Sorted list of candidates above the minimum score.
    """
    candidates: list[AssignmentCandidate] = []
    for agent in request.available_agents:
        if agent.status != AgentStatus.ACTIVE:
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

        # task.assigned_to stores agent ID as string; compare against
        # UUID string form (str(uuid4()) produces lowercase hyphenated)
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
    (descending). Falls back to pure capability sorting when
    no workload data is provided.
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

        if workload_map:
            candidates.sort(
                key=lambda c: (
                    workload_map.get(str(c.agent_identity.id), 0),
                    -c.score,
                ),
            )
            logger.debug(
                TASK_ASSIGNMENT_WORKLOAD_BALANCED,
                task_id=request.task.id,
                agent_name=candidates[0].agent_identity.name,
                workload=workload_map.get(
                    str(candidates[0].agent_identity.id),
                    0,
                ),
            )

        selected = candidates[0]
        alternatives = tuple(candidates[1:])

        return AssignmentResult(
            task_id=request.task.id,
            strategy_used=self.name,
            selected=selected,
            alternatives=alternatives,
            reason=f"Least loaded: {selected.agent_identity.name!r} "
            f"(score={selected.score:.2f})",
        )


# ── Strategy registry ────────────────────────────────────────────
_DEFAULT_SCORER = AgentTaskScorer()

_StrategyType = (
    ManualAssignmentStrategy
    | RoleBasedAssignmentStrategy
    | LoadBalancedAssignmentStrategy
)

STRATEGY_MAP: MappingProxyType[str, _StrategyType] = MappingProxyType(
    {
        STRATEGY_NAME_MANUAL: ManualAssignmentStrategy(),
        STRATEGY_NAME_ROLE_BASED: RoleBasedAssignmentStrategy(
            _DEFAULT_SCORER,
        ),
        STRATEGY_NAME_LOAD_BALANCED: LoadBalancedAssignmentStrategy(
            _DEFAULT_SCORER,
        ),
    },
)
