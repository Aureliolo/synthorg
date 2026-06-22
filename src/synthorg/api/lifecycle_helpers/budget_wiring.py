# module-kind: code
"""Boot wiring for the proactive quota poller.

Constructs and starts the :class:`QuotaPoller` background service once a
persistence backend and the construction-phase :class:`QuotaTracker` are
present. The poller samples provider subscription usage on a cadence and
dispatches WARNING/CRITICAL notifications as thresholds are crossed.

Gated on the ``budget.quota_poller_enabled`` setting (mirrored onto
``QuotaPollerConfig.enabled``, default on) AND a connected persistence
backend AND a wired quota tracker; without them the poller stays absent.
Idempotent for re-entered lifespans (shared-app fixtures): a poller
already on the slice short-circuits. Teardown lives in the on-shutdown
runner (``lifecycle_runner_shutdown``).
"""

from synthorg.api.state import AppState
from synthorg.budget.quota_poller import QuotaPoller
from synthorg.budget.quota_poller_config import QuotaPollerConfig
from synthorg.budget.state import BudgetStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.state import PersistenceStateSlice

logger = get_logger(__name__)


async def wire_quota_poller(app_state: AppState) -> None:
    """Start the proactive quota poller at startup.

    Idempotent for re-entered lifespans: returns early when a poller is
    already wired, persistence is absent, the quota tracker is unwired,
    or the feature toggle is off.

    Args:
        app_state: The application state holding the collaborator slices.
    """
    budget = app_state.slice(BudgetStateSlice)
    if budget.quota_poller is not None:
        return
    if app_state.slice(PersistenceStateSlice).backend is None:
        return
    if budget.quota_tracker is None:
        return
    config = QuotaPollerConfig()
    if not config.enabled:
        return

    poller = QuotaPoller(
        quota_tracker=budget.quota_tracker,
        config=config,
        notification_dispatcher=app_state.slice(NotificationsStateSlice).dispatcher,
        clock=app_state.clock,
    )
    try:
        await poller.start()
        app_state.wire(BudgetStateSlice, quota_poller=poller)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        try:
            await poller.stop()
        except Exception as stop_exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(stop_exc)
            logger.warning(
                API_APP_STARTUP,
                service="quota_poller",
                note="poller rollback-stop failed",
                error_type=type(stop_exc).__name__,
                error=safe_error_description(stop_exc),
            )
        logger.warning(
            API_APP_STARTUP,
            service="quota_poller",
            note="poller start failed; proactive quota alerts disabled",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(API_APP_STARTUP, service="quota_poller", note="wired")


__all__ = ["wire_quota_poller"]
