# module-kind: code
"""Boot wiring for the stakes capability floor.

Both consumers of the floor ask for it here: the stakes gate on the run path
(which refuses an under-capable agent) and the assignment layer (which never
offers it the task in the first place). Building it in one place is what
keeps those two from reaching different verdicts about the same agent, since
both then read the same effective capability map over the same live provider
set.
"""

from synthorg.api.state import AppState
from synthorg.engine.routing_policy.capability_floor import (
    CapabilityFloorPolicy,
    ResolvedAgentCapabilityReader,
)
from synthorg.settings.state import SettingsStateSlice


async def build_capability_floor_policy(
    app_state: AppState,
) -> CapabilityFloorPolicy | None:
    """Build the stakes capability floor from live application state.

    The catalogue is built over the LIVE provider set (the persisted configs,
    falling back to the boot ``RootConfig.providers``) rather than the boot
    snapshot, so a DB-backed deployment gates on the providers actually in
    force. Each model's rung is the effective assignment from the
    :class:`CapabilityAssignmentService`: the heuristic classification
    overlaid by published evidence and by operator / LLM overrides.

    Returns:
        The policy, or ``None`` when no providers are configured (nothing to
        grade, so nothing to gate on).
    """
    from synthorg.providers.routing.resolver import ModelResolver  # noqa: PLC0415
    from synthorg.providers.routing.selector import CheapestSelector  # noqa: PLC0415
    from synthorg.workers._capability_assignment_wiring import (  # noqa: PLC0415
        build_capability_assignment_service,
    )

    config_resolver = app_state.slice(SettingsStateSlice).config_resolver
    if config_resolver is None:
        providers = dict(app_state.config.providers)
    else:
        providers = dict(await config_resolver.get_provider_configs())
    if not providers:
        return None
    capability_service = await build_capability_assignment_service(app_state)
    capability_map = await capability_service.capability_lookup(providers)
    resolver = ModelResolver.from_config(
        providers,
        selector=CheapestSelector(),
        capability_map=capability_map,
    )
    return CapabilityFloorPolicy(
        floors=app_state.config.stakes_routing.stakes_capability_floors,
        reader=ResolvedAgentCapabilityReader(resolver),
    )


__all__ = ["build_capability_floor_policy"]
