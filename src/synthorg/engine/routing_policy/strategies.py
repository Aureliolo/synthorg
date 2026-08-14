"""Stakes-aware and flat routing strategies."""

from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.core.task_enums import Stakes, compare_stakes
from synthorg.core.types import CapabilityLevel
from synthorg.engine.routing_policy.capability_floor import (
    CapabilityFloorPolicy,
    clears_floor,
)
from synthorg.engine.routing_policy.capability_ladder import bump_one
from synthorg.engine.routing_policy.config import StakesRoutingConfig
from synthorg.engine.routing_policy.errors import StakesModelUnavailableError
from synthorg.engine.routing_policy.models import StakesRoutingDecision
from synthorg.observability import get_logger
from synthorg.observability.events.stakes_routing import (
    STAKES_ROUTING_CAPABILITY_ADJUSTED,
    STAKES_ROUTING_COORD_NUDGE,
    STAKES_ROUTING_ESCALATED,
)

logger = get_logger(__name__)


class FlatStrategy:
    """No-op gating: any agent may run any task, no red-team.

    This is the control arm of the cost/quality comparison test and the
    opt-out selectable via the ``flat`` discriminator. It imposes no
    capability requirement, so it can never park a task.
    """

    async def route(
        self,
        *,
        task: Task,
        identity: AgentIdentity,
    ) -> StakesRoutingDecision:
        """Return a decision that imposes no requirement.

        Returns:
            A :class:`StakesRoutingDecision` with no required capability.
        """
        return StakesRoutingDecision(
            agent_capability=identity.model.capability,
            red_team_required=False,
            stakes=task.stakes,
            reason="flat routing: no stakes-based capability requirement",
            source="flat",
        )


class StakesAwareStrategy:
    """Gate a run on the capability the task's stakes demand.

    An agent is a fixed ``(role, personality, model)`` unit: an operator
    chose that ``(provider, model)`` pair for that role, and the pair is what
    makes the agent's history comparable to another agent's. So stakes decide
    what the work needs, and the answer to work that needs more is a
    different agent, never different horsepower behind the same name.

    That leaves this strategy two jobs. It computes the rung the stakes
    demand, bumped one rung when recent coordination metrics look unhealthy.
    And it refuses: an agent below the rung raises
    :class:`StakesModelUnavailableError`, which the engine parks for an
    operator or fails loudly. Consequential work is never quietly upgraded
    onto a model the agent is not, and never quietly run under-capable.

    The rung an agent runs at comes from the capability registry via
    :class:`CapabilityFloorPolicy`, which is the same source the assignment
    layer filters on, so a task cannot be assigned against one verdict and
    then refused against another. What can still differ is the coordination
    nudge: it reads metrics the task only produces once it starts running, so
    a task assigned at its base floor can legitimately be refused later at a
    bumped one. That is a signal arriving, not a disagreement, and parking is
    the honest response to it.

    Deterministic given the capability registry and coordination records;
    performs no wall-clock reads or live provider calls.

    Args:
        floor_policy: Owns the stakes-to-rung floor and reads an agent's own
            rung. Required: the strategy cannot gate without it.
        config: Per-stakes capability floors, nudge thresholds, and the
            red-team threshold.
        coordination_store: Recent coordination metrics for the nudge. When
            ``None``, the nudge is skipped.
    """

    def __init__(
        self,
        *,
        floor_policy: CapabilityFloorPolicy,
        config: StakesRoutingConfig | None = None,
        coordination_store: CoordinationMetricsStore | None = None,
    ) -> None:
        self._floor_policy = floor_policy
        self._config = config or StakesRoutingConfig()
        self._coordination_store = coordination_store

    async def route(
        self,
        *,
        task: Task,
        identity: AgentIdentity,
    ) -> StakesRoutingDecision:
        """Gate *task* on the capability its stakes demand.

        Returns:
            The :class:`StakesRoutingDecision` recording the requirement the
            agent cleared, the red-team requirement, stakes, reasoning effort,
            reason, and source label.

        Raises:
            StakesModelUnavailableError: When the bound agent's model does not
                clear the required capability.
        """
        stakes = task.stakes
        red_team_required = (
            compare_stakes(stakes, self._config.red_team_min_stakes) >= 0
        )
        required, nudged = self._required_capability(task=task, stakes=stakes)
        agent_capability = self._floor_policy.capability_of(identity.model)
        self._report_roster_drift(identity, agent_capability)

        if not clears_floor(agent_capability, required):
            logger.warning(
                STAKES_ROUTING_ESCALATED,
                task_id=str(task.id),
                agent_id=str(identity.id),
                stakes=stakes.value,
                required_capability=required,
                agent_capability=agent_capability,
                reason="assigned_agent_below_capability_floor",
            )
            raise StakesModelUnavailableError(
                stakes=stakes,
                required_capability=required,
            )

        return StakesRoutingDecision(
            required_capability=required,
            agent_capability=agent_capability,
            red_team_required=red_team_required,
            stakes=stakes,
            reasoning_effort=self._config.stakes_reasoning.for_stakes(stakes),
            reason=(
                f"stakes={stakes.value}: agent runs {agent_capability} "
                f"(>= required {required})"
            ),
            source="stakes_aware:nudge" if nudged else "stakes_aware:cleared",
        )

    def _required_capability(
        self,
        *,
        task: Task,
        stakes: Stakes,
    ) -> tuple[CapabilityLevel, bool]:
        """Base stakes floor, bumped when coordination looks unhealthy.

        Returns:
            The required capability, and whether a coordination nudge fired.
        """
        required = self._floor_policy.required_for(stakes)
        if not self._coordination_unhealthy(str(task.id)):
            return required, False
        bumped = bump_one(required)
        if bumped == required:
            return required, False
        logger.info(
            STAKES_ROUTING_COORD_NUDGE,
            task_id=str(task.id),
            from_capability=required,
            to_capability=bumped,
        )
        return bumped, True

    @staticmethod
    def _report_roster_drift(
        identity: AgentIdentity,
        effective: CapabilityLevel | None,
    ) -> None:
        """Log when the roster's rung disagrees with the registry's.

        The roster rung is written when an agent is matched and never
        revised, so an operator override re-grades a model while every roster
        row keeps the old value. Surfacing the drift is what turns a silent
        wrong answer into a visible one; the registry's value is the one
        every gate uses.
        """
        declared = identity.model.capability
        if declared is None or declared == effective:
            return
        logger.info(
            STAKES_ROUTING_CAPABILITY_ADJUSTED,
            agent_id=str(identity.id),
            model_id=identity.model.model_id,
            from_capability=declared,
            to_capability=effective,
            reason="roster_capability_stale",
        )

    def _coordination_unhealthy(self, task_id: str) -> bool:
        """True when recent coordination metrics breach a nudge threshold.

        Returns:
            ``True`` when at least one record in the lookback window shows
            error amplification or overhead past the threshold; ``False``
            otherwise (or when no store is wired).
        """
        if self._coordination_store is None:
            return False
        records, _ = self._coordination_store.query(
            task_id=task_id,
            limit=self._config.coordination_lookback,
        )
        for rec in records:
            amp = rec.metrics.error_amplification
            if (
                amp is not None
                and amp.value > self._config.error_amplification_threshold
            ):
                return True
            overhead = rec.metrics.overhead
            if (
                overhead is not None
                and overhead.value_percent > self._config.overhead_threshold_percent
            ):
                return True
        return False
