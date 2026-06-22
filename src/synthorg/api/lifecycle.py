"""Lifecycle helpers for service startup and shutdown.

The ordered startup (``_safe_startup`` + its persistence-init helpers) lives in
:mod:`synthorg.api.lifecycle_startup`; the shared safe-stop / startup-cleanup
primitives live in :mod:`synthorg.api.lifecycle_shared`. Both are re-exported
here for the historic ``from synthorg.api.lifecycle import ...`` call sites.
This module retains the graceful ordered teardown (``_safe_shutdown``) with its
per-service budgets and the health-prober startup helper.
"""

from synthorg.api.bus_bridge import MessageBusBridge
from synthorg.api.lifecycle_shared import (
    _AsyncStartStop,
    _cleanup_on_failure,
    _try_stop,
)
from synthorg.api.lifecycle_startup import (
    _init_persistence,
    _rebind_connection_catalog,
    _reset_if_tasks_dead,
    _safe_startup,
    _wire_ontology_service,
)
from synthorg.api.state import AppState
from synthorg.backup.models import BackupTrigger
from synthorg.backup.service import BackupService
from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.meeting.scheduler import MeetingScheduler
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.integrations.state import provider_credential_catalog_of
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_SHUTDOWN, API_APP_STARTUP
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.health_prober import ProviderHealthProber
from synthorg.providers.state import (
    ProvidersStateSlice,
    provider_health_tracker_of,
)
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.settings.dispatcher import SettingsChangeDispatcher
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

__all__ = [
    "_DRAIN_TIMEOUT_SECONDS",
    "_AsyncStartStop",
    "_cleanup_on_failure",
    "_init_persistence",
    "_maybe_start_health_prober",
    "_rebind_connection_catalog",
    "_reset_if_tasks_dead",
    "_safe_shutdown",
    "_safe_startup",
    "_try_stop",
    "_wire_ontology_service",
]

logger = get_logger(__name__)


# Per-service shutdown budgets (seconds). The total budget is bounded so
# container orchestrators have headroom before SIGKILL kicks. Worst-case
# shutdown math:
#
#   drain (25)  +  task_engine outer (8 * 2 + 1 = 17)  +
#   meeting (2) +  perf (2)  +  backup (5)  +  settings (2)  +
#   bridge (2)  +  distributed (3)  +  bus (3)  +  persistence (5)  +
#   approval scheduler (1)
#   = 25 + 42 = ~67 s worst case if the drain is held for its full budget AND
#     every service uses its full budget. In practice the drain runs
#     concurrently with no service work and most services return well under
#     their cap. Realistic headline budget is 25 (drain) + ~26 s (services).
#
# This math covers ONLY ``_safe_shutdown`` (the per-service constants below).
# ``_run_shutdown`` runs a preamble of background-service stops BEFORE
# ``_safe_shutdown`` -- including the quota poller and the self-improvement
# service ``close()`` -- and appends the notification-dispatcher + A2A-client
# closes AFTER it. Each of those steps is individually bounded by ``_try_stop``
# (most at ``_SERVICE_STOP_SHUTDOWN_SECONDS`` = 2 s; the draining services at
# ``_DRAINING_SERVICE_STOP_SHUTDOWN_SECONDS``), so each adds at most a couple of
# seconds and stays subsumed under the 75 s ceiling. To keep the aggregate
# inside that ceiling the three independent integration draining services
# (OAuth manager, integration health prober, webhook bridge) drain
# CONCURRENTLY via ``asyncio.gather`` so their cost is one drain budget, not
# three.
#
# Internal constants by design: per-service shutdown budgets enforce a fixed
# total worst-case drain of ~67 s, matched in api/server.py by Litestar's 75 s
# graceful_shutdown to reserve ~8 s of headroom before the orchestrator
# SIGKILLs the process. Raising any individual budget narrows that 8 s headroom
# contract and risks SIGKILL mid-teardown; not exposed to the settings registry
# because the shape of the contract -- not its operator-tunability -- is what
# the orchestrator depends on.
_TASK_ENGINE_SHUTDOWN_SECONDS: float = 8.0
_MEETING_SCHEDULER_SHUTDOWN_SECONDS: float = 2.0
_PERFORMANCE_TRACKER_SHUTDOWN_SECONDS: float = 2.0
_BACKUP_SHUTDOWN_SECONDS: float = 5.0
_SETTINGS_DISPATCHER_SHUTDOWN_SECONDS: float = 2.0
_BRIDGE_SHUTDOWN_SECONDS: float = 2.0
_DISTRIBUTED_QUEUE_SHUTDOWN_SECONDS: float = 3.0
_DISTRIBUTED_BUNDLE_SHUTDOWN_SECONDS: float = 3.0
_MESSAGE_BUS_SHUTDOWN_SECONDS: float = 3.0
_PERSISTENCE_SHUTDOWN_SECONDS: float = 5.0
_APPROVAL_TIMEOUT_SHUTDOWN_SECONDS: float = 1.0
_DRAIN_TIMEOUT_SECONDS: float = 25.0
"""Default budget for :class:`RequestDrainMiddleware`.

Maximum time the on_shutdown drain hook waits for in-flight HTTP requests to
complete after the drain gate flips. Exceeding this budget is logged at WARNING
(``API_APP_DRAIN_TIMEOUT``) and the service-teardown sequence continues.
"""


async def _safe_shutdown(  # noqa: PLR0913
    task_engine: TaskEngine | None,
    meeting_scheduler: MeetingScheduler | None,
    backup_service: BackupService | None,
    approval_timeout_scheduler: ApprovalTimeoutScheduler | None,
    settings_dispatcher: SettingsChangeDispatcher | None,
    bridge: MessageBusBridge | None,
    message_bus: MessageBus | None,
    persistence: PersistenceBackend | None,
    performance_tracker: PerformanceTracker | None = None,
    distributed_task_queue: _AsyncStartStop | None = None,
    distributed_backend_services: _AsyncStartStop | None = None,
) -> None:
    """Stop services in reverse startup order.

    Approval timeout scheduler first, then meeting scheduler (depends on
    orchestrator), then task engine so it can drain queued mutations and publish
    final snapshots through the still-running bridge. The distributed task queue
    stops after the engine so in-flight observer callbacks can still publish
    their final claims. Performance tracker closes after task engine (sampling
    is triggered by task events). Backup runs before persistence disconnect so
    shutdown backup can still access the DB.
    """
    if approval_timeout_scheduler is not None:
        # Inner timeout sets the scheduler's ``_stop_failed`` flag on drain
        # timeout so a subsequent ``start()`` raises rather than spawning a
        # duplicate task on top of an in-flight cancelled one. The outer
        # ``_try_stop`` budget exceeds the inner so the unrestartable guard
        # actually fires before cancellation.
        await _try_stop(
            approval_timeout_scheduler.stop(
                timeout=_APPROVAL_TIMEOUT_SHUTDOWN_SECONDS,
            ),
            API_APP_SHUTDOWN,
            "Failed to stop approval timeout scheduler",
            timeout=_APPROVAL_TIMEOUT_SHUTDOWN_SECONDS * 2.0 + 1.0,
            service="approval_timeout_scheduler",
        )
    if meeting_scheduler is not None:
        await _try_stop(
            meeting_scheduler.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop meeting scheduler",
            timeout=_MEETING_SCHEDULER_SHUTDOWN_SECONDS,
            service="meeting_scheduler",
        )
    if task_engine is not None:
        # ``TaskEngine.stop`` uses an internal hard deadline of
        # ``2 * effective_timeout`` before it sets ``_unrestartable`` and raises
        # ``TimeoutError``. The outer ``_try_stop`` wait must exceed that bound
        # (plus a small slack) so a hung drain actually reaches the
        # unrestartable guard instead of being cancelled by the outer wait.
        await _try_stop(
            task_engine.stop(timeout=_TASK_ENGINE_SHUTDOWN_SECONDS),
            API_APP_SHUTDOWN,
            "Failed to stop task engine",
            timeout=_TASK_ENGINE_SHUTDOWN_SECONDS * 2.0 + 1.0,
            service="task_engine",
        )
    if performance_tracker is not None:
        await _try_stop(
            performance_tracker.aclose(),
            API_APP_SHUTDOWN,
            "Failed to close performance tracker",
            timeout=_PERFORMANCE_TRACKER_SHUTDOWN_SECONDS,
            service="performance_tracker",
        )
    if backup_service is not None:
        if backup_service.on_shutdown:
            try:
                await backup_service.create_backup(
                    BackupTrigger.SHUTDOWN,
                )
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    API_APP_SHUTDOWN,
                    note="Shutdown backup failed (non-fatal)",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        await _try_stop(
            backup_service.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop backup service",
            timeout=_BACKUP_SHUTDOWN_SECONDS,
            service="backup_service",
        )
    if settings_dispatcher is not None:
        await _try_stop(
            settings_dispatcher.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop settings dispatcher",
            timeout=_SETTINGS_DISPATCHER_SHUTDOWN_SECONDS,
            service="settings_dispatcher",
        )
    if bridge is not None:
        await _try_stop(
            bridge.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop message bus bridge",
            timeout=_BRIDGE_SHUTDOWN_SECONDS,
            service="bus_bridge",
        )
    # Backend distributed-path bundle stops before the queue: its dead-letter
    # consumer + heartbeat subscriber read over the queue's NATS connection, so
    # they must release it before the queue drains.
    if distributed_backend_services is not None:
        await _try_stop(
            distributed_backend_services.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop distributed backend services",
            timeout=_DISTRIBUTED_BUNDLE_SHUTDOWN_SECONDS,
            service="distributed_backend_services",
        )
    # Distributed task queue stops after bridge but before the bus so the NATS
    # connection it shares is still alive during drain. This mirrors the exact
    # inverse of the startup order: bus -> queue -> bridge -> ... -> task_engine.
    if distributed_task_queue is not None:
        logger.info(
            API_APP_SHUTDOWN,
            service="distributed_task_queue",
            phase="stopping",
        )
        ok = await _try_stop(
            distributed_task_queue.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop distributed task queue",
            timeout=_DISTRIBUTED_QUEUE_SHUTDOWN_SECONDS,
            service="distributed_task_queue",
        )
        if ok:
            logger.info(
                API_APP_SHUTDOWN,
                service="distributed_task_queue",
                phase="stopped",
            )
    if message_bus is not None:
        await _try_stop(
            message_bus.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop message bus",
            timeout=_MESSAGE_BUS_SHUTDOWN_SECONDS,
            service="message_bus",
        )
    if persistence is not None:
        await _try_stop(
            persistence.disconnect(),
            API_APP_SHUTDOWN,
            "Failed to disconnect persistence",
            timeout=_PERSISTENCE_SHUTDOWN_SECONDS,
            service="persistence",
        )


async def _maybe_start_health_prober(
    app_state: AppState,
) -> ProviderHealthProber | None:
    """Start the health prober if provider tracking is available.

    Non-fatal for non-critical errors (criticals propagate via
    ``reraise_critical``); logs + returns None so serving continues.

    Args:
        app_state: Application state. Requires ``provider_health_tracker`` and
            ``config_resolver``; optionally uses ``provider_management`` for SSRF
            policy.

    Returns:
        The started prober instance, or None if preconditions are not met or a
        non-critical startup error occurs.
    """
    if (
        app_state.slice(ProvidersStateSlice).health_tracker is None
        or app_state.slice(SettingsStateSlice).config_resolver is None
    ):
        logger.debug(
            API_APP_STARTUP,
            note="Health prober skipped: tracker or resolver not available",
        )
        return None
    try:
        management = app_state.slice(ProvidersStateSlice).management
        policy_loader = (
            management.get_discovery_policy if management is not None else None
        )
        prober = ProviderHealthProber(
            health_tracker=provider_health_tracker_of(app_state),
            config_resolver=config_resolver_of(app_state),
            discovery_policy_loader=policy_loader,
            connection_catalog=provider_credential_catalog_of(app_state),
        )
        await prober.start()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            note="Health prober startup failed (non-fatal)",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    return prober
