# module-kind: code
"""Boot wiring for the spend window and the proactive quota poller.

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

from collections.abc import Mapping

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.budget.quota_poller import QuotaPoller
from synthorg.budget.quota_poller_config import QuotaPollerConfig
from synthorg.budget.state import BudgetStateSlice
from synthorg.config.provider_schema import ProviderConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.providers.billing_model_snapshot import ProviderBillingModelSnapshot

logger = get_logger(__name__)


async def hydrate_cost_window(app_state: AppState) -> None:
    """Refill the tracker's spend window from the durable record store.

    The window is what every spend summary and every ceiling reads, and it
    starts empty on each boot. Without this a restart reads as an org that
    has spent nothing, which is the state a ceiling is least able to
    survive.

    A no-op when no tracker or no persistence is wired; the tracker's own
    hydration is best-effort, so a read failure logs rather than failing
    the boot.

    Args:
        app_state: The application state holding the collaborator slices.
    """
    tracker = app_state.slice(BudgetStateSlice).cost_tracker
    if tracker is None or app_state.slice(PersistenceStateSlice).backend is None:
        return
    restored = await tracker.hydrate_from_durable()
    logger.info(
        API_APP_STARTUP,
        service="cost_window",
        note="hydrated",
        restored=restored,
    )


def bind_billing_model_snapshot(
    app_state: AppState,
    provider_configs: Mapping[str, ProviderConfig],
) -> None:
    """Point the ledger at how each configured connection charges.

    Called wherever the provider set is (re)built, so the snapshot the ledger
    stamps from never lags the configs it was built out of.

    Without it every recorded call keeps ``BillingModel.UNKNOWN``, which reads
    as unmeasurable and blanks the money percentage on a perfectly metered
    estate. That is the safe direction to fail (it never reports unmeasurable
    spend as headroom) but it is not a state to ship in, so this is bound on
    the same pass that installs the registry rather than as a later step
    somebody can forget.

    A no-op when no tracker is wired: the trackerless harness records nothing
    to stamp.

    Args:
        app_state: The application state holding the collaborator slices.
        provider_configs: The provider set the registry was built from.
    """
    tracker = app_state.slice(BudgetStateSlice).cost_tracker
    if tracker is None:
        return
    tracker.bind_billing_model_resolver(ProviderBillingModelSnapshot(provider_configs))
    logger.info(
        API_APP_STARTUP,
        service="billing_model_snapshot",
        note="bound",
        provider_count=len(provider_configs),
    )


async def wire_quota_poller(app_state: AppState) -> None:
    """Start the proactive quota poller at startup.

    Returns without doing anything in exactly one case: a poller is
    already wired, which is the re-entered-lifespan idempotency guard and
    reads as up. Every other refusal raises with its condition named, so
    ``GET /subsystems`` answers "why is this not up" without anyone
    reading the wiring log.

    A poller that fails to *start* is the one silent outcome left, and
    deliberately so: the failure is logged, the half-started poller is
    stopped, and the slice stays unwired, so the reconciler reports the
    subsystem down and the next pass retries a transient start failure.

    Args:
        app_state: The application state holding the collaborator slices.

    Raises:
        SubsystemDeclinedError: Polling is switched off, or a collaborator
            it samples through is absent.
    """
    budget = app_state.slice(BudgetStateSlice)
    if budget.quota_poller is not None:
        return
    if app_state.slice(PersistenceStateSlice).backend is None:
        msg = "no persistence backend; quota samples are durable"
        raise SubsystemDeclinedError(msg)
    if budget.quota_tracker is None:
        msg = "no quota tracker; the poller samples through it"
        raise SubsystemDeclinedError(msg)
    config = QuotaPollerConfig()
    if not config.enabled:
        msg = "budget.quota_poller_enabled is off"
        raise SubsystemDeclinedError(msg)

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


__all__ = [
    "bind_billing_model_snapshot",
    "hydrate_cost_window",
    "wire_quota_poller",
]
