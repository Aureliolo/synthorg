"""Work routing policy protocol.

The solo-vs-team decision is owned by the decomposition layer: a
:class:`WorkRoutingPolicy` classifies a task into a
:class:`RoutingVerdict` (``LEAF`` -> single agent, ``SPLITTABLE`` ->
coordinator). Never a user choice.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.engine.pipeline.models import RoutingVerdict


@runtime_checkable
class WorkRoutingPolicy(Protocol):
    """Decides whether a task runs solo (leaf) or as a team.

    Implementations must be deterministic for a given input so the
    decision is reproducible under the simulation harness.
    """

    async def decide(
        self,
        *,
        task: Task,
        available_agents: tuple[AgentIdentity, ...],
    ) -> RoutingVerdict:
        """Return the routing verdict for *task*.

        Args:
            task: The work to classify.
            available_agents: The active agent pool (some policies
                factor pool size or skill coverage into the decision).

        Returns:
            ``RoutingVerdict.LEAF`` or ``RoutingVerdict.SPLITTABLE``.
        """
        ...
