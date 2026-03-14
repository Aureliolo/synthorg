"""Task routing service.

Routes decomposed subtasks to appropriate agents based on
scoring, then selects coordination topology.
"""

from typing import TYPE_CHECKING

from synthorg.engine.routing.models import (
    RoutingDecision,
    RoutingResult,
)
from synthorg.observability import get_logger
from synthorg.observability.events.task_routing import (
    TASK_ROUTING_COMPLETE,
    TASK_ROUTING_FAILED,
    TASK_ROUTING_NO_AGENTS,
    TASK_ROUTING_STARTED,
    TASK_ROUTING_SUBTASK_ROUTED,
    TASK_ROUTING_SUBTASK_UNROUTABLE,
)

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task
    from synthorg.engine.decomposition.models import DecompositionResult
    from synthorg.engine.routing.scorer import AgentTaskScorer
    from synthorg.engine.routing.topology_selector import TopologySelector

logger = get_logger(__name__)


class TaskRoutingService:
    """Routes subtasks to agents based on skill matching.

    For each subtask in a decomposition result, scores all available
    agents and selects the best match. Subtasks with no viable
    candidate are reported as unroutable.
    """

    __slots__ = ("_scorer", "_topology_selector")

    def __init__(
        self,
        scorer: AgentTaskScorer,
        topology_selector: TopologySelector,
    ) -> None:
        self._scorer = scorer
        self._topology_selector = topology_selector

    def route(
        self,
        decomposition_result: DecompositionResult,
        available_agents: tuple[AgentIdentity, ...],
        parent_task: Task,
    ) -> RoutingResult:
        """Route all subtasks to appropriate agents.

        For each subtask:
        1. Score all available agents.
        2. Select the best candidate (highest score >= min_score).
        3. Select topology from parent task override or plan structure.
        4. Report unroutable subtasks.

        Args:
            decomposition_result: The decomposition to route.
            available_agents: Pool of agents to consider.
            parent_task: The parent task (for topology selection).

        Returns:
            Routing result with decisions and unroutable subtask IDs.
        """
        plan = decomposition_result.plan

        if parent_task.id != plan.parent_task_id:
            msg = (
                f"parent_task.id {parent_task.id!r} does not "
                f"match plan.parent_task_id "
                f"{plan.parent_task_id!r}"
            )
            logger.warning(
                TASK_ROUTING_FAILED,
                parent_task_id=parent_task.id,
                plan_parent_task_id=plan.parent_task_id,
                error=msg,
            )
            raise ValueError(msg)

        logger.info(
            TASK_ROUTING_STARTED,
            parent_task_id=plan.parent_task_id,
            subtask_count=len(plan.subtasks),
            agent_count=len(available_agents),
        )

        if not available_agents:
            logger.warning(
                TASK_ROUTING_NO_AGENTS,
                parent_task_id=plan.parent_task_id,
                subtask_count=len(plan.subtasks),
            )
            return RoutingResult(
                parent_task_id=plan.parent_task_id,
                unroutable=tuple(s.id for s in plan.subtasks),
            )

        try:
            return self._do_route(decomposition_result, available_agents, parent_task)
        except Exception:
            logger.exception(
                TASK_ROUTING_FAILED,
                parent_task_id=plan.parent_task_id,
            )
            raise

    def _do_route(
        self,
        decomposition_result: DecompositionResult,
        available_agents: tuple[AgentIdentity, ...],
        parent_task: Task,
    ) -> RoutingResult:
        """Internal routing logic.

        Args:
            decomposition_result: The decomposition to route.
            available_agents: Pool of agents to consider.
            parent_task: The parent task (for topology selection).

        Returns:
            Routing result with decisions and unroutable subtask IDs.
        """
        plan = decomposition_result.plan
        topology = self._topology_selector.select(parent_task, plan)

        decisions: list[RoutingDecision] = []
        unroutable: list[str] = []

        for subtask_def in plan.subtasks:
            candidates = [
                self._scorer.score(agent, subtask_def) for agent in available_agents
            ]

            # Filter by minimum score and sort descending
            viable = sorted(
                [c for c in candidates if c.score >= self._scorer.min_score],
                key=lambda c: c.score,
                reverse=True,
            )

            if not viable:
                logger.warning(
                    TASK_ROUTING_SUBTASK_UNROUTABLE,
                    subtask_id=subtask_def.id,
                    agent_count=len(available_agents),
                )
                unroutable.append(subtask_def.id)
                continue

            selected = viable[0]
            alternatives = tuple(viable[1:])

            decision = RoutingDecision(
                subtask_id=subtask_def.id,
                selected_candidate=selected,
                alternatives=alternatives,
                topology=topology,
            )
            decisions.append(decision)

            logger.debug(
                TASK_ROUTING_SUBTASK_ROUTED,
                subtask_id=subtask_def.id,
                agent_name=selected.agent_identity.name,
                score=selected.score,
                alternatives=len(alternatives),
            )

        result = RoutingResult(
            parent_task_id=plan.parent_task_id,
            decisions=tuple(decisions),
            unroutable=tuple(unroutable),
        )

        logger.info(
            TASK_ROUTING_COMPLETE,
            parent_task_id=plan.parent_task_id,
            routed=len(decisions),
            unroutable=len(unroutable),
            topology=topology.value,
        )

        return result
