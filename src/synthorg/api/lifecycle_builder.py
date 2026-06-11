"""Startup/shutdown lifecycle builder for the Litestar application.

``_build_lifecycle`` binds the two-phase wiring runners to the per-app service
handles and a shared :class:`_LifecycleTasks` state container, returning the
``(on_startup, on_shutdown)`` callback lists Litestar drives. The runner bodies
live in :mod:`api.lifecycle_runner_startup` / :mod:`api.lifecycle_runner_shutdown`
so this module stays a thin entry point; the shared wiring helpers are
re-exported here for the existing import sites.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING

from synthorg.api.lifecycle_runner_shutdown import _run_shutdown
from synthorg.api.lifecycle_runner_startup import _run_startup
from synthorg.api.lifecycle_runner_support import (
    _cancel_with_timeout,
    _LifecycleTasks,
    _wire_approval_gate,
    _wire_workflow_observer,
)
from synthorg.api.state import AppState
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.api import (
    API_AUDIT_RETENTION,
    API_WS_TICKET_CLEANUP,
)
from synthorg.observability.events.persistence.webhook_receipt import (
    PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP,
)

if TYPE_CHECKING:
    from synthorg.api.bus_bridge import MessageBusBridge
    from synthorg.backup.service import BackupService
    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.communication.meeting.scheduler import MeetingScheduler
    from synthorg.config.schema import RootConfig
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.persistence.protocol import PersistenceBackend
    from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
    from synthorg.settings.dispatcher import SettingsChangeDispatcher

__all__ = [
    "_build_lifecycle",
    "_cancel_with_timeout",
    "_wire_approval_gate",
    "_wire_workflow_observer",
]

logger = get_logger(__name__)


def _make_cleanup_done_callback(
    event: str,
    message: str,
) -> Callable[[asyncio.Task[None]], None]:
    """Build a task-done callback that logs under a domain event.

    The ticket-cleanup and audit-retention loops both want the same "log if it
    died unexpectedly" semantics but need different observability event names so
    a compliance-affecting retention outage is not mis-routed to the WebSocket
    cleanup channel.

    Args:
        event: Observability event name for the unexpected-death log.
        message: Human-readable note attached to the log.

    Returns:
        A done-callback that logs when the task ended with an exception.
    """

    def _callback(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log_exception_redacted(logger, event, exc, note=message)

    return _callback


def _build_lifecycle(  # noqa: PLR0913
    persistence: PersistenceBackend | None,
    message_bus: MessageBus | None,
    bridge: MessageBusBridge | None,
    settings_dispatcher: SettingsChangeDispatcher | None,
    task_engine: TaskEngine | None,
    meeting_scheduler: MeetingScheduler | None,
    backup_service: BackupService | None,
    approval_timeout_scheduler: ApprovalTimeoutScheduler | None,
    app_state: AppState,
    *,
    should_auto_wire_settings: bool = False,
    effective_config: RootConfig | None = None,
) -> tuple[
    Sequence[Callable[[], Awaitable[None]]],
    Sequence[Callable[[], Awaitable[None]]],
]:
    """Build startup and shutdown hooks.

    Args:
        persistence: Persistence backend (``None`` when unconfigured).
        message_bus: Internal message bus (``None`` when unconfigured).
        bridge: Message bus bridge to WebSocket channels.
        settings_dispatcher: Settings change dispatcher.
        task_engine: Centralized task state engine.
        meeting_scheduler: Meeting scheduler service.
        backup_service: Backup and restore service.
        approval_timeout_scheduler: Background approval timeout checker.
        app_state: Application state container.
        should_auto_wire_settings: When ``True``, on-startup auto-wiring creates
            ``SettingsService`` + dispatcher after persistence connects.
        effective_config: Root config needed for on-startup auto-wiring.

    Returns:
        A tuple of (on_startup, on_shutdown) callback lists.
    """
    tasks = _LifecycleTasks()
    on_ticket_cleanup_done = _make_cleanup_done_callback(
        API_WS_TICKET_CLEANUP,
        "Ticket cleanup task died unexpectedly",
    )
    on_audit_retention_done = _make_cleanup_done_callback(
        API_AUDIT_RETENTION,
        "Audit retention task died unexpectedly",
    )
    on_webhook_cleanup_done = _make_cleanup_done_callback(
        PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP,
        "Webhook receipt cleanup task died unexpectedly",
    )

    async def on_startup() -> None:
        await _run_startup(
            tasks,
            app_state,
            persistence=persistence,
            message_bus=message_bus,
            bridge=bridge,
            settings_dispatcher=settings_dispatcher,
            task_engine=task_engine,
            meeting_scheduler=meeting_scheduler,
            backup_service=backup_service,
            approval_timeout_scheduler=approval_timeout_scheduler,
            should_auto_wire_settings=should_auto_wire_settings,
            effective_config=effective_config,
            on_ticket_cleanup_done=on_ticket_cleanup_done,
            on_audit_retention_done=on_audit_retention_done,
            on_webhook_cleanup_done=on_webhook_cleanup_done,
        )

    async def on_shutdown() -> None:
        await _run_shutdown(
            tasks,
            app_state,
            persistence=persistence,
            message_bus=message_bus,
            bridge=bridge,
            settings_dispatcher=settings_dispatcher,
            task_engine=task_engine,
            meeting_scheduler=meeting_scheduler,
            backup_service=backup_service,
            approval_timeout_scheduler=approval_timeout_scheduler,
        )

    return [on_startup], [on_shutdown]
