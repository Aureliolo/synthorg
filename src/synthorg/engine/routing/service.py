"""Task routing service.

Routes decomposed subtasks to appropriate agents: the capability ladder
narrows the pool to the band that best fits what each subtask demands, the
scorer ranks within it, and the topology is selected once for the wave.

The ladder runs here rather than only at dispatch because assignment and
dispatch must reach the same verdict. Routing a subtask to an agent the
dispatch will then refuse is the two-owner shape: the quieter authority wins
and the operator sees a parked task with no assignment reason.
"""

from synthorg.core.agent import AgentIdentity
from synthorg.core.capability_fit import partition_by_fit
from synthorg.core.task import Task
from synthorg.engine.decomposition.models import (
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.routing.models import (
    RoutingDecision,
    RoutingResult,
)
from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.engine.routing.topology_selector import TopologySelector
from synthorg.engine.routing_policy.capability_policy import (
    CapabilityPolicy,
    rank_of,
)
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.task_assignment import (
    TASK_ASSIGNMENT_UNDER_CAPABILITY,
)
from synthorg.observability.events.task_routing import (
    TASK_ROUTING_COMPLETE,
    TASK_ROUTING_FAILED,
    TASK_ROUTING_NO_AGENTS,
    TASK_ROUTING_STARTED,
    TASK_ROUTING_SUBTASK_ROUTED,
    TASK_ROUTING_SUBTASK_UNROUTABLE,
)

logger = get_logger(__name__)


class TaskRoutingService:
    """Routes subtasks to agents by capability fit, then by score.

    For each subtask in a decomposition result, narrows the available agents
    to the band that best fits the capability the subtask demands, scores
    that band, and selects the best match. Subtasks with no viable candidate
    are reported as unroutable.

    Args:
        scorer: Ranks candidates within whichever capability band answers.
        topology_selector: Chooses the wave's coordination topology.
        capability: The org's one capability policy, shared with the solo
            assignment path and with dispatch. ``None`` (a pipeline built
            without one) routes on score alone.
    """

    __slots__ = ("_capability", "_scorer", "_topology_selector")

    def __init__(
        self,
        scorer: AgentTaskScorer,
        topology_selector: TopologySelector,
        *,
        capability: CapabilityPolicy | None = None,
    ) -> None:
        self._scorer = scorer
        self._topology_selector = topology_selector
        self._capability = capability

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

        Raises:
            ValueError: When the topology cannot be resolved from
                the parent task's override and plan structure.
        """
        plan = decomposition_result.plan

        if str(parent_task.id) != plan.parent_task_id:
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
        except Exception as exc:
            log_exception_redacted(
                logger, TASK_ROUTING_FAILED, exc, parent_task_id=plan.parent_task_id
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
            band = self._capable_band(subtask_def, available_agents)
            candidates = [self._scorer.score(agent, subtask_def) for agent in band]

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
                    capable_count=len(band),
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

    def _capable_band(
        self,
        subtask: SubtaskDefinition,
        available_agents: tuple[AgentIdentity, ...],
    ) -> tuple[AgentIdentity, ...]:
        """Narrow *available_agents* to the band that best fits *subtask*.

        The same ladder the solo path walks, off the same policy instance:
        agents at the exact rung the subtask demands, else the nearest rung
        above, else (where the stakes allow) the nearest rung below with the
        concession logged. Agents the stakes forbid are dropped first, so a
        critical subtask goes unroutable rather than landing on a weaker
        agent that dispatch would refuse.

        Returns:
            The surviving agents, or the pool unchanged when no policy is
            wired.
        """
        if self._capability is None:
            return available_agents
        policy = self._capability
        sanctioned = tuple(
            agent
            for agent in available_agents
            if policy.judge(
                model=agent.model,
                stakes=subtask.stakes,
                complexity=subtask.estimated_complexity,
            ).sanctioned
        )
        required = policy.required_for(subtask.stakes, subtask.estimated_complexity)
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
                subtask_id=subtask.id,
                path="coordination",
                stakes=subtask.stakes.value,
                required_capability=required,
                band_capability=policy.capability_of(band[0].model),
                candidates=len(band),
                note=(
                    "No agent runs at or above the rung this subtask demands; "
                    "routed to the strongest available agent instead."
                ),
            )
        return band
