# module-kind: code
"""The on-shutdown runner: ordered teardown of the lifecycle-owned services.

``_run_shutdown`` is the body of the historic ``on_shutdown`` closure lifted to
a top-level function. Its former ``nonlocal`` janitor-task / dispatcher /
health-prober / training-backend state now lives on the shared
:class:`_LifecycleTasks` container threaded in by the builder.
"""

from collections.abc import Awaitable
from typing import cast

from synthorg.a2a.state import A2aStateSlice
from synthorg.api.bus_bridge import MessageBusBridge
from synthorg.api.lifecycle import _safe_shutdown, _try_stop
from synthorg.api.lifecycle_runner_support import (
    _cancel_with_timeout,
    _LifecycleTasks,
)
from synthorg.api.state import AppState
from synthorg.backup.service import BackupService
from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.meeting.scheduler import MeetingScheduler
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.state import HrStateSlice
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.memory.state import MemoryStateSlice
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_SHUTDOWN
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.settings.dispatcher import SettingsChangeDispatcher
from synthorg.workers.state import RuntimeStateSlice

logger = get_logger(__name__)


# Per-task shutdown budgets for the three janitor loops launched by the
# lifecycle builder. These are passive wake-poll-sleep loops so 2.0s matches the
# budget already used for the meeting scheduler / settings dispatcher /
# bus-bridge in ``api/lifecycle.py``. Wrapping the cancel-and-await with
# ``asyncio.wait_for`` keeps shutdown bounded even when a task body shields
# ``CancelledError`` (third-party callees, hung I/O); the orchestrator's SIGKILL
# deadline must not slip past ``graceful_shutdown`` (75s in api/server.py).
_TICKET_CLEANUP_SHUTDOWN_SECONDS: float = 2.0
_AUDIT_RETENTION_SHUTDOWN_SECONDS: float = 2.0
_WEBHOOK_CLEANUP_SHUTDOWN_SECONDS: float = 2.0


async def _run_shutdown(  # noqa: PLR0913
    tasks: _LifecycleTasks,
    app_state: AppState,
    *,
    persistence: PersistenceBackend | None,
    message_bus: MessageBus | None,
    bridge: MessageBusBridge | None,
    settings_dispatcher: SettingsChangeDispatcher | None,
    task_engine: TaskEngine | None,
    meeting_scheduler: MeetingScheduler | None,
    backup_service: BackupService | None,
    approval_timeout_scheduler: ApprovalTimeoutScheduler | None,
) -> None:
    """Run the ordered on-shutdown teardown.

    Args:
        tasks: Shared mutable handles the startup runner populated.
        app_state: Application state container.
        persistence: Persistence backend (``None`` when unconfigured).
        message_bus: Internal message bus (``None`` when unconfigured).
        bridge: Message bus bridge to WebSocket channels.
        settings_dispatcher: Settings change dispatcher.
        task_engine: Centralized task state engine.
        meeting_scheduler: Meeting scheduler service.
        backup_service: Backup and restore service.
        approval_timeout_scheduler: Background approval timeout checker.
    """
    # Emit the shutdown event before any teardown step so the gate-crossing is
    # observable even if a downstream stop hangs or raises. Mirrors the
    # ``on_startup`` emission at the top of that function.
    from synthorg import __version__  # noqa: PLC0415

    logger.info(API_APP_SHUTDOWN, version=__version__)
    # Drain in-flight parked-context resumes (background tasks spawned off the
    # /approvals path) before teardown so an approved resume is not silently
    # dropped mid-flight. Read the runtime slice field directly; only the
    # agent-runtime service exposes ``drain_resume_tasks`` (the no-provider
    # backstop does not), so the ``getattr`` guard stays.
    _wes = app_state.slice(RuntimeStateSlice).worker_execution_service
    _drain_resumes = getattr(_wes, "drain_resume_tasks", None)
    if callable(_drain_resumes):
        await _try_stop(
            cast("Awaitable[None]", _drain_resumes()),
            API_APP_SHUTDOWN,
            "Failed to drain in-flight parked-context resumes",
        )
    # Drain in-flight gated-completion background tasks (the red-team
    # evaluation dispatched off the /approvals path) so an approved
    # completion is not cancelled mid-evaluation and stranded in IN_REVIEW.
    from synthorg.approval.state import ApprovalStateSlice  # noqa: PLC0415

    _review_gate = app_state.slice(ApprovalStateSlice).review_gate
    if _review_gate is not None:
        await _try_stop(
            _review_gate.drain_background_tasks(),
            API_APP_SHUTDOWN,
            "Failed to drain in-flight gated-completion background tasks",
        )
    # Disconnect training memory backend if auto-wired.
    if tasks.training_memory_backend is not None:
        # If this backend was published to the memory slice at startup, clear
        # the field before disconnecting so a subsequent re-entry of the
        # lifespan can wire a fresh connected backend without a stale handle
        # lingering on the slice.
        shared = app_state.slice(MemoryStateSlice).backend
        if shared is tasks.training_memory_backend:
            app_state.swap_slice(
                app_state.slice(MemoryStateSlice).model_copy(update={"backend": None})
            )
        disconnect = getattr(tasks.training_memory_backend, "disconnect", None)
        if callable(disconnect):
            # getattr + callable narrow statically only to ``object`` and
            # "something callable", so the return type isn't inferable.
            # Backends that expose a ``disconnect`` method always return
            # ``Awaitable[None]`` by contract (see ``MemoryBackend.disconnect``
            # in training/memory).
            await _try_stop(
                cast("Awaitable[None]", disconnect()),
                API_APP_SHUTDOWN,
                "Failed to disconnect training memory backend",
            )
        tasks.training_memory_backend = None
    if tasks.ticket_cleanup_task is not None:
        await _cancel_with_timeout(
            tasks.ticket_cleanup_task,
            service="ticket_cleanup",
            timeout=_TICKET_CLEANUP_SHUTDOWN_SECONDS,
        )
        tasks.ticket_cleanup_task = None
    if tasks.audit_retention_task is not None:
        await _cancel_with_timeout(
            tasks.audit_retention_task,
            service="audit_retention",
            timeout=_AUDIT_RETENTION_SHUTDOWN_SECONDS,
        )
        tasks.audit_retention_task = None
    if tasks.webhook_cleanup_task is not None:
        await _cancel_with_timeout(
            tasks.webhook_cleanup_task,
            service="webhook_cleanup",
            timeout=_WEBHOOK_CLEANUP_SHUTDOWN_SECONDS,
        )
        tasks.webhook_cleanup_task = None
    communication = app_state.slice(CommunicationStateSlice)
    integrations = app_state.slice(IntegrationsStateSlice)
    if communication.event_stream_hub is not None:
        await _try_stop(
            communication.event_stream_hub.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop event stream hub",
        )
    if tasks.health_prober is not None:
        await _try_stop(
            tasks.health_prober.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop health prober",
        )
        tasks.health_prober = None
    # Stop integration background services (reverse start order).
    if communication.escalation_notify_subscriber is not None:
        await _try_stop(
            communication.escalation_notify_subscriber.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop escalation notify subscriber",
        )
    if communication.escalation_sweeper is not None:
        await _try_stop(
            communication.escalation_sweeper.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop escalation sweeper",
        )
    # Cancel any unresolved pending futures so coroutines awaiting operator
    # decisions get a clean CancelledError (instead of hanging past shutdown)
    # and the registry map is emptied.
    if communication.escalation_registry is not None:
        await _try_stop(
            communication.escalation_registry.close(),
            API_APP_SHUTDOWN,
            "Failed to close escalation pending-futures registry",
        )
    if integrations.oauth_token_manager is not None:
        await _try_stop(
            integrations.oauth_token_manager.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop OAuth token manager",
        )
    if integrations.health_prober_service is not None:
        await _try_stop(
            integrations.health_prober_service.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop integration health prober",
        )
    if integrations.webhook_event_bridge is not None:
        await _try_stop(
            integrations.webhook_event_bridge.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop webhook event bridge",
        )
    if integrations.tunnel_provider is not None:
        await _try_stop(
            integrations.tunnel_provider.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop tunnel provider",
        )
    from synthorg.meta.toolsmith.state import ToolsmithStateSlice  # noqa: PLC0415

    toolsmith = app_state.slice(ToolsmithStateSlice)
    if toolsmith.cycle_scheduler is not None:
        await _try_stop(
            toolsmith.cycle_scheduler.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop toolsmith cycle scheduler",
        )
        # Clear the service too, not just the scheduler: wire_toolsmith
        # short-circuits when service is already set, so leaving it populated
        # would skip re-wiring on the next lifespan entry (hot-reload / tests).
        app_state.swap_slice(
            toolsmith.model_copy(update={"service": None, "cycle_scheduler": None}),
        )
    # Stop every cached rate-limit coordinator and clear the module-level
    # factory so background poll tasks and bus subscriptions cannot outlive the
    # app (matters for hot-reload / test teardown where ``create_app`` runs
    # multiple times in the same process).
    try:
        from synthorg.integrations.rate_limiting import (  # noqa: PLC0415
            shared_state as _rate_limit_shared_state,
        )

        await _rate_limit_shared_state.set_coordinator_factory(None)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_SHUTDOWN,
            phase="rate_limit_coordinator_stop",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    if tasks.auto_wired_dispatcher is not None:
        await _try_stop(
            tasks.auto_wired_dispatcher.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop auto-wired settings dispatcher",
        )
        tasks.auto_wired_dispatcher = None
    await _safe_shutdown(
        task_engine,
        meeting_scheduler,
        backup_service,
        approval_timeout_scheduler,
        settings_dispatcher,
        bridge,
        message_bus,
        persistence,
        performance_tracker=app_state.slice(HrStateSlice).performance_tracker,
        distributed_task_queue=app_state.slice(
            RuntimeStateSlice
        ).distributed_task_queue,
        distributed_backend_services=app_state.slice(
            RuntimeStateSlice
        ).distributed_backend_services,
    )
    notification_dispatcher = app_state.slice(NotificationsStateSlice).dispatcher
    if notification_dispatcher is not None:
        await _try_stop(
            notification_dispatcher.aclose(),
            API_APP_SHUTDOWN,
            "Failed to stop notification dispatcher",
        )
    # Close A2A outbound HTTP client if wired.
    try:
        a2a_client_obj = app_state.slice(A2aStateSlice).client
        if a2a_client_obj is not None and hasattr(a2a_client_obj, "aclose"):
            await a2a_client_obj.aclose()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_SHUTDOWN,
            phase="a2a_client_close",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
