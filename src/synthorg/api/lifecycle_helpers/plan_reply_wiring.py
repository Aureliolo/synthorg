# module-kind: orchestrator
"""Startup wiring for the conversational plan-item reply service.

Separate from the approval-gate wiring it used to sit beside: that module
owns whether a plan parks for a human, this one owns whether the human's
comments get answered. They share a settings namespace and nothing else.
"""

from typing import Final

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)

#: Wall-clock cap for one plan-item reply completion. A reply is a single
#: bounded call, so it shares the order of a normal agent call timeout.
_REPLY_TIMEOUT_SECONDS: Final[float] = 120.0


async def wire_plan_item_reply_service(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    cost_tracker: CostTrackerProtocol | None,
) -> None:
    """Wire the conversational plan-item reply service when a model is set.

    Built unconditionally of ``plan_review_reply_enabled`` so the live
    per-comment gate can flip without a restart.

    Raises:
        SubsystemDeclinedError: No provider registry, or no
            ``plan_review_reply_model`` that a registered provider serves.
            Plan comments then go unanswered, which is a state an operator
            fixes by naming a model, so the reason is reported rather than
            the boot being failed over it.
    """
    from synthorg.engine.plan_review.reply import (  # noqa: PLC0415
        build_plan_item_reply_service,
    )
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

    if provider_registry is None:
        msg = "no provider registry is wired, so no model can serve a reply"
        raise SubsystemDeclinedError(msg)
    resolver = config_resolver_of(app_state)
    service = build_plan_item_reply_service(
        reply_model=await resolver.get_str("coordination", "plan_review_reply_model"),
        temperature=await resolver.get_float(
            "coordination", "plan_review_reply_temperature"
        ),
        max_tokens=await resolver.get_int(
            "coordination", "plan_review_reply_max_tokens"
        ),
        timeout_seconds=_REPLY_TIMEOUT_SECONDS,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
        config_resolver=resolver,
    )
    if service is None:
        msg = (
            "unset: coordination.plan_review_reply_model, or no registered "
            "provider serves the pair it names"
        )
        raise SubsystemDeclinedError(msg)
    app_state.wire(EngineStateSlice, plan_item_reply_service=service)
    logger.info(API_APP_STARTUP, service="plan_item_reply_service", note="wired")


async def unwire_plan_item_reply_service(app_state: AppState) -> None:
    """Drop the reply service so the next pass rebuilds it.

    The service bakes its provider driver in at construction, so replacing
    the instance is what makes ``plan_review_reply_model`` live in both
    directions: renaming or clearing the pair retargets a service that is
    already answering, which the per-call live re-read cannot do because its
    fallback is the build-time pair it is trying to leave.
    """
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

    app_state.wire(EngineStateSlice, plan_item_reply_service=None)
    logger.info(API_APP_STARTUP, service="plan_item_reply_service", note="unwired")
