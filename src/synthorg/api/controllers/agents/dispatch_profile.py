# module-kind: controller
"""Per-agent dispatch comparison: how each agent's own calls actually went.

Kept off :class:`AgentObservabilityController` (already near its
module-size budget) so this concern grows on its own controller; both
mount under ``/agents`` and the literal ``/dispatch-profiles`` route
resolves ahead of ``/{agent_id}``.
"""

from litestar import Controller, get
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.controllers.agents._shared import _require_registered_identity
from synthorg.api.dto import DEFAULT_LIMIT, ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import PathId
from synthorg.api.state import AppState
from synthorg.core.agent import AgentIdentity
from synthorg.hr.state import agent_registry_of
from synthorg.providers.dispatch_profile import (
    DEFAULT_MIN_CALLS_FOR_PROFILE,
    DispatchProfile,
    build_dispatch_profile,
)
from synthorg.providers.health import ProviderHealthRecord
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import SettingsStateSlice

_MIN_CALLS_KEY = "agent_profile_min_calls"


async def _min_calls(app_state: AppState) -> int:
    """Return the sample floor a profile is judged against.

    Read live rather than snapshotted: an operator lowering the floor while
    a roster is new should see the numbers on the next read.

    Returns:
        The configured floor, or the shipped default when no resolver is
        wired (a test harness or an anonymous boot has no operator value to
        read, and reporting every profile as sufficient would be worse than
        reporting the default).
    """
    resolver = app_state.slice(SettingsStateSlice).config_resolver
    if resolver is None:
        return DEFAULT_MIN_CALLS_FOR_PROFILE
    return await resolver.get_int(SettingNamespace.PROVIDERS.value, _MIN_CALLS_KEY)


class AgentDispatchProfileController(Controller):
    """Per-agent and roster-wide dispatch comparison reads."""

    path = "/agents"
    tags = ("agents",)
    guards = [require_read_access]  # noqa: RUF012

    @get("/dispatch-profiles")
    async def list_dispatch_profiles(
        self,
        state: State,
        limit: CursorLimit = DEFAULT_LIMIT,
        cursor: CursorParam = None,
    ) -> PaginatedResponse[DispatchProfile]:
        """Compare every active agent's own dispatch record.

        One snapshot of the record store serves the whole roster: agents
        share models and a comparison page should not cost a read per row.

        Returns:
            ``PaginatedResponse`` wrapping one profile per active agent,
            each carrying its own sample size.
        """
        app_state: AppState = state.app_state
        actives = await agent_registry_of(app_state).list_active()
        floor = await _min_calls(app_state)
        tracker = app_state.slice(ProvidersStateSlice).health_tracker
        profiles: list[DispatchProfile] = []
        for agent in actives:
            made: tuple[ProviderHealthRecord, ...] = (
                ()
                if tracker is None
                else await tracker.records_for_agent(str(agent.id))
            )
            profiles.append(build_dispatch_profile(agent, made, min_calls=floor))
        page, meta = paginate_cursor(
            tuple(profiles),
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        return PaginatedResponse(data=page, pagination=meta)

    @get("/{agent_id:str}/dispatch-profile")
    async def get_dispatch_profile(
        self,
        state: State,
        agent_id: PathId,
    ) -> ApiResponse[DispatchProfile]:
        """Report one agent's own dispatch record.

        Returns:
            The agent's profile, with a zero call count when it has made no
            real calls in the window (which is a true statement about a new
            agent, not an absence to 404 on).

        Raises:
            NotFoundError: If the agent is not registered.
        """
        app_state: AppState = state.app_state
        identity: AgentIdentity = await _require_registered_identity(
            app_state, agent_id
        )
        tracker = require_service(
            app_state.slice(ProvidersStateSlice).health_tracker,
            "Provider Health Tracker",
        )
        records: tuple[ProviderHealthRecord, ...] = await tracker.records_for_agent(
            str(identity.id)
        )
        return ApiResponse(
            data=build_dispatch_profile(
                identity, records, min_calls=await _min_calls(app_state)
            )
        )


__all__ = ["AgentDispatchProfileController"]
