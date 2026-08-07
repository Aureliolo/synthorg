# module-kind: code
"""On-startup wiring for the two per-turn routing components.

The turn-intent classifier and the multi-voice chime-in router are declared
as subsystems of their own rather than as steps inside the Chief-of-Staff
proposer's activation. The reconciler leaves an already-active subsystem
alone, so a classifier wired from inside that activation could never appear
after the proposer was up, which is exactly when an operator names the model.
Declared separately, a blank model leaves each one inactive and the write that
fills it is the change that brings it up.

Both are shaped identically (resolve a model, build a component, wire one
slice field, tear it down again) and both are read per turn from the same
endpoint, so they live together and out of ``conversational_wiring``, which
owns the write-path services and its own size tier.
"""

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.meta.config import SelfImprovementConfig
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)


async def wire_turn_intent_classifier(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    cost_tracker: CostTrackerProtocol | None,
    si_config: SelfImprovementConfig,
) -> None:
    """Build + wire the unified turn-intent classifier when a model is set.

    ``build_intent_classifier`` builds the classifier unconditionally of
    ``turn_router_enabled`` so the live per-request gate (applied in the
    ``/meta/chat/turn`` endpoint) can flip without a restart; it returns
    ``None`` only when no ``turn_intent_model`` is configured or its bound
    provider is absent, leaving the unified router to answer every turn as a
    plain question.

    Raises:
        SubsystemDeclinedError: When it cannot be built, naming which of the
            two causes applied. Named here rather than left to the
            reconciler's guess because "the model is set but its provider is
            not registered" and "no model is set" are different operator
            actions, and the dogfood could tell neither from the status.
    """
    from synthorg.meta.chief_of_staff.intent_router import (  # noqa: PLC0415
        build_intent_classifier,
    )
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    if provider_registry is None:
        msg = "waiting on: provider registry"
        raise SubsystemDeclinedError(msg)
    classifier = build_intent_classifier(
        config=si_config.chief_of_staff,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
        config_resolver=app_state.slice(SettingsStateSlice).config_resolver,
    )
    if classifier is None:
        raise SubsystemDeclinedError(
            _model_decline("chief_of_staff.turn_intent_model", si_config)
        )
    app_state.wire(MetaStateSlice, turn_intent_classifier=classifier)
    logger.info(
        API_APP_STARTUP,
        service="turn_intent_classifier",
        note="turn intent classifier wired",
    )


def _model_decline(setting: str, si_config: SelfImprovementConfig) -> str:
    """Explain why a component bound to *setting* could not be built.

    Args:
        setting: The dotted ``chief_of_staff.*_model`` key it binds.
        si_config: The config the build read.

    Returns:
        A message naming the operator action: set the model, or register the
        provider the model already names.
    """
    _, _, field = setting.partition(".")
    value = str(getattr(si_config.chief_of_staff, field, "") or "")
    if not value:
        return f"unset: {setting}"
    return (
        f"{setting} names a provider that is not registered; "
        "add the connection or choose a model on a registered one"
    )


async def unwire_turn_intent_classifier(app_state: AppState) -> None:
    """Take the classifier down so the next pass rebuilds it.

    Paired with the wirer on any change to ``turn_intent_model``, which is
    what makes clearing the model live: without a teardown the previous
    classifier keeps classifying on its build-time pair, so the feature
    could be switched on without a restart but never off.
    """
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

    app_state.wire(MetaStateSlice, turn_intent_classifier=None)
    logger.info(
        API_APP_STARTUP,
        service="turn_intent_classifier",
        note="turn intent classifier unwired",
    )


async def wire_multi_voice_router(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    cost_tracker: CostTrackerProtocol | None,
    si_config: SelfImprovementConfig,
) -> None:
    """Build + wire the multi-voice chime-in router when a model is set.

    ``build_multi_voice_router`` builds the router unconditionally of
    ``multi_voice_enabled`` so the live per-turn gate can flip without a
    restart; it returns ``None`` only when no ``multi_voice_model`` is set or
    its bound provider is absent, leaving turns to carry no chime-ins.

    Raises:
        SubsystemDeclinedError: When it cannot be built, naming which of the
            two causes applied.
    """
    from synthorg.meta.chief_of_staff._multi_voice import (  # noqa: PLC0415
        build_multi_voice_router,
    )
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    if provider_registry is None:
        msg = "waiting on: provider registry"
        raise SubsystemDeclinedError(msg)
    router = build_multi_voice_router(
        config=si_config.chief_of_staff,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
        config_resolver=app_state.slice(SettingsStateSlice).config_resolver,
    )
    if router is None:
        raise SubsystemDeclinedError(
            _model_decline("chief_of_staff.multi_voice_model", si_config)
        )
    app_state.wire(MetaStateSlice, multi_voice_router=router)
    logger.info(
        API_APP_STARTUP,
        service="multi_voice_router",
        note="multi-voice router wired",
    )


async def unwire_multi_voice_router(app_state: AppState) -> None:
    """Take the multi-voice router down so the next pass rebuilds it."""
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

    app_state.wire(MetaStateSlice, multi_voice_router=None)
    logger.info(
        API_APP_STARTUP,
        service="multi_voice_router",
        note="multi-voice router unwired",
    )


__all__ = [
    "unwire_multi_voice_router",
    "unwire_turn_intent_classifier",
    "wire_multi_voice_router",
    "wire_turn_intent_classifier",
]
