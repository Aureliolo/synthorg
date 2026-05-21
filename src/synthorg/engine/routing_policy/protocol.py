"""Pluggable stakes-aware routing strategy protocol."""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task
    from synthorg.engine.routing_policy.models import StakesRoutingDecision


@runtime_checkable
class StakesRoutingStrategy(Protocol):
    """Picks a model (and red-team requirement) from a task's stakes.

    Implementations are deterministic given their injected score and
    metric sources, so the cost/quality comparison test is reproducible.
    """

    async def route(
        self,
        *,
        task: Task,
        identity: AgentIdentity,
    ) -> StakesRoutingDecision:
        """Return the routing decision for *task* run by *identity*."""
        ...
