"""Always-team routing policy.

Forces every unit of work onto the multi-agent coordinator. Used
when an operator mandates team execution and as a deterministic
fixture for the team-branch acceptance test.
"""

from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.engine.pipeline.models import RoutingVerdict
from synthorg.observability import get_logger
from synthorg.observability.events.pipeline import PIPELINE_ROUTING_DECIDED

logger = get_logger(__name__)


class AlwaysTeamRoutingPolicy:
    """Always returns :attr:`RoutingVerdict.SPLITTABLE`."""

    __slots__ = ()

    async def decide(
        self,
        *,
        task: Task,
        available_agents: tuple[AgentIdentity, ...],
    ) -> RoutingVerdict:
        """Return ``SPLITTABLE`` unconditionally."""
        del available_agents
        logger.info(
            PIPELINE_ROUTING_DECIDED,
            task_id=task.id,
            policy="always-team",
            verdict=RoutingVerdict.SPLITTABLE.value,
        )
        return RoutingVerdict.SPLITTABLE
