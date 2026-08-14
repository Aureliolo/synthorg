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

Kept out of :class:`AgentController` (config-time and already at its
module-size baseline) so the runtime-roster concern grows on its own
controller; both mount under ``/agents`` and the literal ``/active``
route resolves ahead of ``/{agent_id}``.
"""

from collections.abc import Mapping

from litestar import Controller, get
from litestar.datastructures import State
from pydantic import BaseModel, ConfigDict, computed_field

from synthorg.api.dto import DEFAULT_LIMIT, PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.hr.state import agent_registry_of
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import HR_AGENT_HEALTH_FAILED
from synthorg.providers.agent_availability import (
    AgentUnavailability,
    unavailability_by_pair,
)
from synthorg.providers.state import ProvidersStateSlice

logger = get_logger(__name__)


async def _unavailable_pairs(
    app_state: AppState,
) -> Mapping[tuple[str, str], AgentUnavailability]:
    """Read every unserviceable pair, treating a read failure as available.

    The verdict only annotates each row, so a health-surface fault must not
    take the roster with it: the engine path already rules that way
    (``ServiceabilityFilteredRoster``), and a roster that 500s because the
    tracker is unwell is strictly worse than one reporting nobody out.

    Returns:
        The pairs that cannot serve; empty when nothing measures them or
        the read failed.
    """
    tracker = app_state.slice(ProvidersStateSlice).health_tracker
    if tracker is None:
        return {}
    try:
        return unavailability_by_pair(await tracker.get_all_serviceability())
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            HR_AGENT_HEALTH_FAILED,
            operation="availability_read",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return {}


class ActiveAgentSummary(BaseModel):
    """Lean identity summary for one active registered agent.

    Carries only what the dashboard needs to reference an agent by its
    stable runtime id (and show who it is); the full identity (model,
    personality, authority) is deliberately not exposed on a roster read.

    Attributes:
        id: The agent's stable runtime identifier (``AgentIdentity.id``).
        name: Human-readable display name.
        role: Role label (e.g. ``CFO``).
        unavailable: Why the agent cannot take work, or ``None`` when it
            can. Carried on the roster because an agent that is out is not
            a rendering detail: the operator picking a participant, or
            wondering why work is parking, needs to see it here.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    name: NotBlankStr
    role: NotBlankStr
    unavailable: AgentUnavailability | None = None

    @computed_field(description="Whether the agent can take work now")
    @property
    def is_available(self) -> bool:
        """Whether the agent's bound model can currently serve."""
        return self.unavailable is None


class AgentRosterController(Controller):
    """Read-only runtime agent-roster endpoints."""

    path = "/agents"
    tags = ("agents",)
    guards = [require_read_access]  # noqa: RUF012

    @get("/active")
    async def list_active_agents(
        self,
        state: State,
        limit: CursorLimit = DEFAULT_LIMIT,
        cursor: CursorParam = None,
    ) -> PaginatedResponse[ActiveAgentSummary]:
        """List active registered agents with their runtime UUIDs.

        Unlike ``GET /agents`` (the config-time roster, which has no
        ids), this reads the live registry so each agent carries its
        stable ``AgentIdentity.id``. Cursor-paginated so a large active
        roster (the group-chat participant picker) returns a bounded page.

        Returns:
            ``PaginatedResponse`` wrapping the active agents.
        """
        app_state = state.app_state
        registry = agent_registry_of(app_state)
        actives = await registry.list_active()
        # One fleet-wide read joined by pair, rather than a serviceability
        # lookup per row: agents share models, and a roster page should not
        # cost a snapshot per agent to answer one question about each.
        out = await _unavailable_pairs(app_state)
        summaries = tuple(
            ActiveAgentSummary(
                id=NotBlankStr(str(agent.id)),
                name=agent.name,
                role=agent.role,
                unavailable=out.get((agent.model.provider, agent.model.model_id)),
            )
            for agent in actives
        )
        page, meta = paginate_cursor(
            summaries,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        return PaginatedResponse(data=page, pagination=meta)
