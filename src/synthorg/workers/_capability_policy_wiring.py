# module-kind: code
"""Boot wiring for the one capability policy.

Every consumer of "what does this work need, and may this agent take it" asks
the SAME instance: the solo assignment service, the coordination router, the
completion-oracle and red-team gates, the review-staffing sweep, and the
dispatch binding. Building it once here is what keeps them from reaching
different verdicts about the same agent, since they then read the same
effective capability map over the same live provider set, and one
:meth:`CapabilityPolicy.set_config` call re-points all of them when an
operator edits the ladder.
"""

from synthorg.api.state import AppState
from synthorg.engine.routing_policy.capability_policy import (
    CapabilityPolicy,
    ResolvedAgentCapabilityReader,
)
from synthorg.settings._resolver_capability_policy import (
    resolve_capability_policy_config,
)
from synthorg.settings.state import SettingsStateSlice


async def build_capability_policy(
    app_state: AppState,
) -> CapabilityPolicy | None:
    """Return the process's capability policy, building it on first ask.

    Memoised onto the engine slice rather than rebuilt per consumer: two
    instances would be two answers to "what rung does this agent run at", and
    the settings subscriber could only ever re-point one of them.

    The catalogue is built over the LIVE provider set (the persisted configs,
    falling back to the boot ``RootConfig.providers``) rather than the boot
    snapshot, so a DB-backed deployment judges on the providers actually in
    force. Each model's rung is the effective assignment from the
    :class:`CapabilityAssignmentService`: the heuristic classification
    overlaid by published evidence and by operator / LLM overrides.

    Returns:
        The policy, or ``None`` when no providers are configured (nothing to
        grade, so nothing to judge against).
    """
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.providers.routing.resolver import ModelResolver  # noqa: PLC0415
    from synthorg.providers.routing.selector import CheapestSelector  # noqa: PLC0415
    from synthorg.workers._capability_assignment_wiring import (  # noqa: PLC0415
        build_capability_assignment_service,
    )

    existing = app_state.slice(EngineStateSlice).capability_policy
    if existing is not None:
        return existing

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
    policy_config = (
        app_state.config.capability_policy
        if config_resolver is None
        else await resolve_capability_policy_config(config_resolver)
    )
    policy = CapabilityPolicy(
        config=policy_config,
        reader=ResolvedAgentCapabilityReader(resolver),
    )
    app_state.wire(EngineStateSlice, capability_policy=policy)
    return policy


__all__ = ["build_capability_policy"]
