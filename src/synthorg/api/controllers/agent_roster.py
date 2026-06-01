# module-kind: controller
"""Runtime agent-roster read endpoint (active registered agents).

The config-sourced ``GET /agents`` list returns the configured roster
with no runtime identifier -- a configured agent has no UUID until it is
registered into the runtime registry. This surface reads the REGISTRY's
active agents and exposes each one's stable runtime ``AgentIdentity.id``
(a UUID) so the dashboard can reference a specific agent by id rather
than by its mutable, non-unique display name. The multi-agent group
chat participant picker is the first consumer: it sends the
selected ids to ``POST /meta/chat/group``.

Kept out of :class:`AgentController` (config-time, name-addressed, and
already at its module-size baseline) so the runtime-roster concern grows
on its own controller; both mount under ``/agents`` and the literal
``/active`` route resolves ahead of ``/{agent_name}``.
"""

from litestar import Controller, get
from litestar.datastructures import State
from pydantic import BaseModel, ConfigDict

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.core.types import NotBlankStr
from synthorg.hr.state import agent_registry_of


class ActiveAgentSummary(BaseModel):
    """Lean identity summary for one active registered agent.

    Carries only what the dashboard needs to reference an agent by its
    stable runtime id (and show who it is); the full identity (model,
    personality, authority) is deliberately not exposed on a roster read.

    Attributes:
        id: The agent's stable runtime identifier (``AgentIdentity.id``).
        name: Human-readable display name.
        role: Role label (e.g. ``CFO``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    name: NotBlankStr
    role: NotBlankStr


class AgentRosterController(Controller):
    """Read-only runtime agent-roster endpoints."""

    path = "/agents"
    tags = ("agents",)
    guards = [require_read_access]  # noqa: RUF012

    @get("/active")
    async def list_active_agents(
        self, state: State
    ) -> ApiResponse[tuple[ActiveAgentSummary, ...]]:
        """List active registered agents with their runtime UUIDs.

        Unlike ``GET /agents`` (the config-time roster, which has no
        ids), this reads the live registry so each agent carries its
        stable ``AgentIdentity.id``. An org with no active agents returns
        an empty list (not an error).

        Returns:
            ``ApiResponse`` wrapping the active agents.
        """
        registry = agent_registry_of(state.app_state)
        actives = await registry.list_active()
        return ApiResponse(
            data=tuple(
                ActiveAgentSummary(
                    id=NotBlankStr(str(agent.id)),
                    name=agent.name,
                    role=agent.role,
                )
                for agent in actives
            )
        )
