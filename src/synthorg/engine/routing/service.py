"""Task routing service.

Routes decomposed subtasks to appropriate agents: the capability ladder
narrows the pool to the band that best fits what each subtask demands, the
scorer ranks within it, and the topology is selected once for the wave.

The ladder runs here rather than only at dispatch because assignment and
dispatch must reach the same verdict. Routing a subtask to an agent the
dispatch will then refuse is the two-owner shape: the quieter authority wins
and the operator sees a parked task with no assignment reason.
"""

from collections.abc import Sequence
from typing import NamedTuple

from synthorg.core.agent import AgentIdentity
from synthorg.core.capability_fit import bands_by_fit
from synthorg.core.task import Task
from synthorg.engine.decomposition.models import (
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.routing.models import (
    RoutingCandidate,
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


class _Admissible(NamedTuple):
    """Who may take a subtask, and whose binding could not be judged at all."""

    admitted: tuple[AgentIdentity, ...]
    unresolved: tuple[AgentIdentity, ...]


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
        # Every level: a recursive decomposition holds most of its work below
        # the root, and a container needs an owner exactly as a leaf does.
        units = decomposition_result.all_subtasks

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
            subtask_count=len(units),
            agent_count=len(available_agents),
        )

        if not available_agents:
            logger.warning(
                TASK_ROUTING_NO_AGENTS,
                parent_task_id=plan.parent_task_id,
                subtask_count=len(units),
            )
            return RoutingResult(
                parent_task_id=plan.parent_task_id,
                unroutable=tuple(s.id for s in units),
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

        for subtask_def in decomposition_result.all_subtasks:
            viable = self._viable_candidates(subtask_def, available_agents)

            if not viable:
                admissible = self._sanctioned(subtask_def, available_agents)
                logger.warning(
                    TASK_ROUTING_SUBTASK_UNROUTABLE,
                    subtask_id=subtask_def.id,
                    agent_count=len(available_agents),
                    # Every agent the stakes admit, across every rung, since
                    # the ladder is walked to the end before giving up.
                    capable_count=len(admissible.admitted),
                    required_role=subtask_def.required_role,
                    sanctioned_roles=sorted({a.role for a in admissible.admitted}),
                    # Named apart from the rest of the refusals: an agent here
                    # is not too weak for THIS subtask, it is unusable for
                    # every subtask in the org until its binding is fixed, and
                    # its roster row says nothing about that.
                    unresolved_bindings=sorted(
                        f"{a.role}:{a.model.provider}/{a.model.model_id}"
                        for a in admissible.unresolved
                    ),
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

    def _sanctioned(
        self,
        subtask: SubtaskDefinition,
        available_agents: tuple[AgentIdentity, ...],
    ) -> _Admissible:
        """Split the pool into who may take *subtask* and who cannot, and why.

        Applied before any banding, so a critical subtask goes unroutable
        rather than landing on a weaker agent that dispatch would refuse.

        The two refusals are kept apart because they are answered by different
        people. Too weak for these stakes is a routing outcome. An UNRESOLVED
        binding is a misconfiguration: the pair is in no catalogue and the
        roster gave it no rung, so the agent is refused for every subtask in
        the org at every stakes level, and shows on the roster as available
        while being assignable nothing. The policy already distinguishes them
        and says the refusal exists so the problem is "named where it can be
        reported"; folding both into one boolean is what left it unnamed.

        Returns:
            The admissible agents and the unresolved bindings among the rest.
        """
        if self._capability is None:
            return _Admissible(available_agents, ())
        policy = self._capability
        admitted: list[AgentIdentity] = []
        unresolved: list[AgentIdentity] = []
        for agent in available_agents:
            verdict = policy.judge(
                model=agent.model,
                stakes=subtask.stakes,
                complexity=subtask.estimated_complexity,
            )
            if verdict.sanctioned:
                admitted.append(agent)
            elif verdict.unresolved:
                unresolved.append(agent)
        return _Admissible(tuple(admitted), tuple(unresolved))

    def _viable_candidates(
        self,
        subtask: SubtaskDefinition,
        available_agents: tuple[AgentIdentity, ...],
    ) -> list[RoutingCandidate]:
        """Score *subtask* down the capability ladder until someone is viable.

        The ladder is a preference and never a filter: the exact rung is tried
        first (which is the standing cost discipline, picking the cheapest
        candidate that can do the work), then each rung above, then each rung
        below the stakes still admit. Scoring inside one band and stopping made
        the preference absolute, so an over-qualified specialist was unreachable
        while any exact-rung stranger existed, and the subtask went unroutable
        with a capable agent idle.

        Returns:
            The viable candidates from the first band that yields any, best
            score first; empty when no rung does.
        """
        sanctioned = self._sanctioned(subtask, available_agents).admitted
        if self._capability is None:
            return self._score_band(sanctioned, subtask)
        policy = self._capability
        required = policy.required_for(subtask.stakes, subtask.estimated_complexity)
        for band, fit in bands_by_fit(
            sanctioned,
            lambda agent: rank_of(policy.capability_of(agent.model)),
            rank_of(required),
        ):
            viable = self._score_band(band, subtask)
            if not viable:
                continue
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
                        "No agent at or above the rung this subtask demands "
                        "was viable; routed to the strongest available agent."
                    ),
                )
            return viable
        return []

    def _score_band(
        self,
        band: Sequence[AgentIdentity],
        subtask: SubtaskDefinition,
    ) -> list[RoutingCandidate]:
        """Score one band and keep what clears the floor, best first.

        Returns:
            The viable candidates in descending score order.
        """
        return sorted(
            (
                candidate
                for candidate in (self._scorer.score(a, subtask) for a in band)
                if candidate.score >= self._scorer.min_score
            ),
            key=lambda c: c.score,
            reverse=True,
        )
