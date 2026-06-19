# module-kind: code
"""Boot wiring for the automatic promotion subsystem.

Constructs :class:`PromotionService` from the agent registry +
performance tracker (with the approval + trust services when present)
once those collaborators are wired, then starts a
:class:`PromotionCycleScheduler` so the org re-evaluates agent seniority
on a cadence rather than only on a manual trigger. Mirrors
``wire_model_refresh``: the scheduler is started BEFORE the AppState
slice is published and rolled back on failure, and the whole step is
idempotent for re-entered lifespans (shared-app fixtures).

Gated on ``PromotionConfig.enabled`` (default on) AND a wired registry +
tracker; without them the service stays absent and its controller / MCP
handlers honestly 503.
"""

from synthorg.api.state import AppState
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.hr.promotion.config import PromotionConfig
from synthorg.hr.promotion.cycle_scheduler import PromotionCycleScheduler
from synthorg.hr.promotion.factory import build_promotion_service
from synthorg.hr.state import HrStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.security.state import SecurityStateSlice
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)


async def wire_promotion(
    app_state: AppState,
    *,
    config: PromotionConfig,
) -> None:
    """Wire the promotion service + cycle scheduler at startup.

    Idempotent for re-entered lifespans: returns early when a service is
    already wired.

    Args:
        app_state: The application state holding the collaborator slices.
        config: The effective promotion configuration.
    """
    hr = app_state.slice(HrStateSlice)
    if hr.promotion_service is not None:
        return
    if not config.enabled:
        return
    if hr.agent_registry is None or hr.performance_tracker is None:
        logger.warning(
            API_APP_STARTUP,
            service="promotion",
            note="registry or tracker absent; promotion disabled",
        )
        return

    service = build_promotion_service(
        registry=hr.agent_registry,
        tracker=hr.performance_tracker,
        config=config,
        approval_store=app_state.slice(ApprovalStateSlice).store,
        trust_service=app_state.slice(SecurityStateSlice).trust_service,
    )
    scheduler = PromotionCycleScheduler(
        service,
        interval_seconds=config.cycle_interval_seconds,
        config_resolver=app_state.slice(SettingsStateSlice).config_resolver,
    )
    try:
        await scheduler.start()
        app_state.wire(
            HrStateSlice,
            promotion_service=service,
            promotion_cycle_scheduler=scheduler,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        try:
            await scheduler.stop()
        except Exception as stop_exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(stop_exc)
            logger.warning(
                API_APP_STARTUP,
                service="promotion",
                note="scheduler rollback-stop failed",
                error_type=type(stop_exc).__name__,
                error=safe_error_description(stop_exc),
            )
        logger.warning(
            API_APP_STARTUP,
            service="promotion",
            note="scheduler start failed; promotion disabled",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(API_APP_STARTUP, service="promotion", note="wired")


__all__ = ["wire_promotion"]
