# module-kind: code
"""Shared lifecycle primitives: the safe-stop helper + startup-failure cleanup.

``_try_stop`` is the per-service safe-await used by both the startup-failure
cleanup and the ordered shutdown; ``_cleanup_on_failure`` tears down the
already-started services in reverse order when ``_safe_startup`` raises. Split
into a leaf module so :mod:`api.lifecycle_startup` and :mod:`api.lifecycle`
both import them without an import cycle.
"""

import asyncio
from collections.abc import Awaitable
from typing import Final, Protocol

from synthorg.api.bus_bridge import MessageBusBridge
from synthorg.backup.service import BackupService
from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.meeting.scheduler import MeetingScheduler
from synthorg.core.lifecycle_constants import DEFAULT_DRAIN_TIMEOUT_SECONDS
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.settings.dispatcher import SettingsChangeDispatcher

logger = get_logger(__name__)

# Per-service stop budget for the runtime background services cleaned up on a
# startup failure. The lifecycle-lock services (health probers, OAuth token
# manager, webhook event bridge) can drain in-flight work up to
# ``DEFAULT_DRAIN_TIMEOUT_SECONDS``; the quick poll-loop services (event
# stream hub, tunnel provider) finish well inside it. A single bounded budget
# keeps any one hung stop from blocking the rest of the reverse cleanup.
_CLEANUP_STOP_TIMEOUT_SECONDS: Final[float] = DEFAULT_DRAIN_TIMEOUT_SECONDS


# Structural seam over the optional synthorg[distributed] JetStreamTaskQueue;
# consumers: _cleanup_on_failure, _safe_shutdown.
class _AsyncStartStop(Protocol):
    """Minimal async lifecycle Protocol used by the distributed task queue hook.

    The concrete type is ``synthorg.workers.claim.JetStreamTaskQueue``, but
    importing that here would force the optional ``synthorg[distributed]`` extra
    to be installed even for deployments that never use the queue. A structural
    Protocol with ``start()``/``stop()`` gives the lifecycle helpers a real
    shape without the hard dependency.
    """

    async def start(self) -> None:
        """Open the connection / initialise resources."""
        ...

    async def stop(self) -> None:
        """Tear down the connection / release resources."""
        ...


async def _try_stop(
    coro: Awaitable[None],
    event: str,
    error_msg: str,
    *,
    timeout: float | None = None,  # noqa: ASYNC109 -- per-service shutdown budget
    service: str | None = None,
) -> bool:
    """Await *coro* inside a safe try/except, logging failures.

    ``MemoryError`` and ``RecursionError`` are re-raised immediately; all other
    exceptions are logged and swallowed so that sibling shutdown steps can still
    run.

    When *timeout* is set, a ``TimeoutError`` is logged at ERROR with the
    ``service`` label (when provided) and shutdown continues with the next
    service. Services that hang past their per-service budget must not block the
    whole shutdown window.

    Args:
        coro: The stop/disconnect coroutine to await.
        event: Log event name for the failure branch.
        error_msg: Human-readable context for the failure log.
        timeout: Optional per-service budget (seconds).
        service: Optional service label for the structured log.

    Returns:
        ``True`` when *coro* completes without raising, ``False`` when an
        exception was swallowed. Callers use this to guard "stopped" log lines
        so they only fire on actual success.

    Raises:
        MemoryError: Re-raised unchanged (never swallowed).
        RecursionError: Re-raised unchanged (never swallowed).
    """
    awaitable = asyncio.wait_for(coro, timeout=timeout) if timeout is not None else coro
    try:
        await awaitable
    except MemoryError, RecursionError:
        raise
    except TimeoutError as exc:
        from synthorg.observability.events.api import (  # noqa: PLC0415
            API_APP_SHUTDOWN_TIMEOUT,
        )

        # logger.error rather than logger.exception so TimeoutError frame-locals
        # never serialize the awaited coroutine's state (which may include
        # secret-bearing shutdown objects).
        log_exception_redacted(
            logger,
            API_APP_SHUTDOWN_TIMEOUT,
            exc,
            service=service,
            timeout_seconds=timeout,
            context=error_msg,
        )
        return False
    except Exception as exc:  # noqa: BLE001 -- shutdown best-effort: log and continue
        logger.warning(
            event,
            service=service,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            context=error_msg,
        )
        return False
    return True


async def _cleanup_on_failure(  # noqa: PLR0913
    *,
    persistence: PersistenceBackend | None,
    started_persistence: bool,
    message_bus: MessageBus | None,
    started_bus: bool,
    bridge: MessageBusBridge | None = None,
    started_bridge: bool = False,
    settings_dispatcher: SettingsChangeDispatcher | None = None,
    started_settings_dispatcher: bool = False,
    task_engine: TaskEngine | None = None,
    started_task_engine: bool = False,
    distributed_task_queue: _AsyncStartStop | None = None,
    started_distributed_task_queue: bool = False,
    distributed_backend_services: _AsyncStartStop | None = None,
    started_distributed_backend_services: bool = False,
    meeting_scheduler: MeetingScheduler | None = None,
    started_meeting_scheduler: bool = False,
    backup_service: BackupService | None = None,
    started_backup_service: bool = False,
    approval_timeout_scheduler: ApprovalTimeoutScheduler | None = None,
    started_approval_timeout_scheduler: bool = False,
    event_stream_hub: _AsyncStartStop | None = None,
    started_event_stream_hub: bool = False,
    escalation_notify_subscriber: _AsyncStartStop | None = None,
    started_escalation_notify_subscriber: bool = False,
    escalation_sweeper: _AsyncStartStop | None = None,
    started_escalation_sweeper: bool = False,
    oauth_token_manager: _AsyncStartStop | None = None,
    started_oauth_token_manager: bool = False,
    integration_health_prober: _AsyncStartStop | None = None,
    started_integration_health_prober: bool = False,
    webhook_event_bridge: _AsyncStartStop | None = None,
    started_webhook_event_bridge: bool = False,
    provider_health_prober: _AsyncStartStop | None = None,
    started_provider_health_prober: bool = False,
) -> None:
    """Reverse cleanup on startup failure.

    The runtime background services (event stream hub, integration services,
    health probers) start AFTER the core services, so they are stopped FIRST
    here -- in reverse of their ``_run_startup`` start order. Each stop is
    bounded by ``_CLEANUP_STOP_TIMEOUT_SECONDS`` so a hung drain cannot block
    the rest of the reverse cleanup. Every runtime-service param defaults to
    ``None`` / ``False`` so the core-only ``_safe_startup`` failure path
    (which never started them) passes nothing and the blocks no-op.
    """
    if started_event_stream_hub and event_stream_hub is not None:
        await _try_stop(
            event_stream_hub.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop event stream hub",
            timeout=_CLEANUP_STOP_TIMEOUT_SECONDS,
            service="event_stream_hub",
        )
    if (
        started_escalation_notify_subscriber
        and escalation_notify_subscriber is not None
    ):
        await _try_stop(
            escalation_notify_subscriber.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop escalation notify subscriber",
            timeout=_CLEANUP_STOP_TIMEOUT_SECONDS,
            service="escalation_notify_subscriber",
        )
    if started_escalation_sweeper and escalation_sweeper is not None:
        await _try_stop(
            escalation_sweeper.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop escalation sweeper",
            timeout=_CLEANUP_STOP_TIMEOUT_SECONDS,
            service="escalation_sweeper",
        )
    if started_oauth_token_manager and oauth_token_manager is not None:
        await _try_stop(
            oauth_token_manager.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop OAuth token manager",
            timeout=_CLEANUP_STOP_TIMEOUT_SECONDS,
            service="oauth_token_manager",
        )
    if started_integration_health_prober and integration_health_prober is not None:
        await _try_stop(
            integration_health_prober.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop integration health prober",
            timeout=_CLEANUP_STOP_TIMEOUT_SECONDS,
            service="integration_health_prober",
        )
    if started_webhook_event_bridge and webhook_event_bridge is not None:
        await _try_stop(
            webhook_event_bridge.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop webhook event bridge",
            timeout=_CLEANUP_STOP_TIMEOUT_SECONDS,
            service="webhook_event_bridge",
        )
    if started_provider_health_prober and provider_health_prober is not None:
        await _try_stop(
            provider_health_prober.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop provider health prober",
            timeout=_CLEANUP_STOP_TIMEOUT_SECONDS,
            service="provider_health_prober",
        )
    if started_approval_timeout_scheduler and approval_timeout_scheduler is not None:
        await _try_stop(
            approval_timeout_scheduler.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop approval timeout scheduler",
        )
    if started_backup_service and backup_service is not None:
        await _try_stop(
            backup_service.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop backup service",
        )
    if started_meeting_scheduler and meeting_scheduler is not None:
        await _try_stop(
            meeting_scheduler.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop meeting scheduler",
        )
    if started_task_engine and task_engine is not None:
        await _try_stop(
            task_engine.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop task engine",
        )
    if started_settings_dispatcher and settings_dispatcher is not None:
        await _try_stop(
            settings_dispatcher.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop settings dispatcher",
        )
    if started_bridge and bridge is not None:
        await _try_stop(
            bridge.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop message bus bridge",
        )
    if (
        started_distributed_backend_services
        and distributed_backend_services is not None
    ):
        await _try_stop(
            distributed_backend_services.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop distributed backend services",
        )
    if started_distributed_task_queue and distributed_task_queue is not None:
        logger.info(
            API_APP_STARTUP,
            service="distributed_task_queue",
            phase="stopping_on_cleanup",
        )
        ok = await _try_stop(
            distributed_task_queue.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop distributed task queue",
        )
        if ok:
            logger.info(
                API_APP_STARTUP,
                service="distributed_task_queue",
                phase="stopped_on_cleanup",
            )
    if started_bus and message_bus is not None:
        await _try_stop(
            message_bus.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop message bus",
        )
    if started_persistence and persistence is not None:
        await _try_stop(
            persistence.disconnect(),
            API_APP_STARTUP,
            "Cleanup: failed to disconnect persistence",
        )
