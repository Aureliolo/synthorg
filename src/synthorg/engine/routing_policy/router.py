"""Stakes router service: delegates to a strategy and logs decisions."""

from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.engine.routing_policy.models import StakesRoutingDecision
from synthorg.engine.routing_policy.protocol import StakesRoutingStrategy
from synthorg.observability import get_logger
from synthorg.observability.events.stakes_routing import (
    STAKES_ROUTING_DECIDED,
    STAKES_ROUTING_RED_TEAM_MARKED,
)

logger = get_logger(__name__)


class StakesRouter:
    """Injectable seam wrapping a :class:`StakesRoutingStrategy`.

    The engine calls :meth:`route` before the budget auto-downgrade to
    obtain the stakes-adjusted model and the red-team requirement.

    Args:
        strategy: The routing strategy to delegate to.
    """

    __slots__ = ("_strategy",)

    def __init__(self, strategy: StakesRoutingStrategy) -> None:
        self._strategy = strategy

    async def route(
        self,
        *,
        task: Task,
        identity: AgentIdentity,
    ) -> StakesRoutingDecision:
        """Return the stakes-aware routing decision for *task*."""
        decision = await self._strategy.route(task=task, identity=identity)
        logger.info(
            STAKES_ROUTING_DECIDED,
            task_id=task.id,
            agent_id=str(identity.id),
            stakes=decision.stakes.value,
            from_model=identity.model.model_id,
            to_model=decision.selected_model.model_id,
            to_tier=decision.selected_model.model_tier,
            source=decision.source,
            red_team_required=decision.red_team_required,
        )
        if decision.red_team_required:
            logger.info(
                STAKES_ROUTING_RED_TEAM_MARKED,
                task_id=task.id,
                stakes=decision.stakes.value,
            )
        return decision
