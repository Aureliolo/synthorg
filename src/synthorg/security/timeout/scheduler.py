"""Approval timeout scheduler -- periodic background approval checking.

Polls the ``ApprovalStore`` for PENDING items and evaluates each
against the configured ``TimeoutPolicy`` via ``TimeoutChecker``.
When an item times out, the scheduler applies the policy action
(approve, deny, or escalate) and invokes an optional callback
for downstream resume/review-gate logic.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable  # noqa: TC003
from typing import TYPE_CHECKING

from synthorg.core.enums import ApprovalStatus, TimeoutActionType
from synthorg.notifications.dispatcher import NotificationDispatcher  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.background_tasks import BackgroundTaskRegistry
from synthorg.observability.events.notification import NOTIFICATION_ESCALATION_SEND
from synthorg.observability.events.timeout import (
    TIMEOUT_SCHEDULER_ERROR,
    TIMEOUT_SCHEDULER_RESCHEDULED,
    TIMEOUT_SCHEDULER_RESOLVED,
    TIMEOUT_SCHEDULER_STARTED,
    TIMEOUT_SCHEDULER_STOPPED,
    TIMEOUT_SCHEDULER_TICK,
)

if TYPE_CHECKING:
    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.core.approval import ApprovalItem
    from synthorg.security.timeout.models import TimeoutAction
    from synthorg.security.timeout.timeout_checker import TimeoutChecker

logger = get_logger(__name__)


class ApprovalTimeoutScheduler:
    """Background asyncio task that checks pending approvals for timeout.

    Periodically polls the ``ApprovalStore`` for PENDING items and
    evaluates each against the configured ``TimeoutPolicy`` via
    ``TimeoutChecker``.

    Args:
        approval_store: Store to poll for pending items.
        timeout_checker: Evaluates items against the timeout policy.
        interval_seconds: Seconds between poll cycles.
        on_timeout_resolve: Async callback invoked when a timeout
            action resolves an approval (APPROVE or DENY).
        notification_dispatcher: Optional dispatcher for out-of-band
            operator alerts on escalation.
    """

    def __init__(
        self,
        *,
        approval_store: ApprovalStoreProtocol,
        timeout_checker: TimeoutChecker,
        interval_seconds: float,
        on_timeout_resolve: (
            Callable[[ApprovalItem, TimeoutAction], Awaitable[None]] | None
        ) = None,
        notification_dispatcher: NotificationDispatcher | None = None,
    ) -> None:
        # ``interval_seconds`` is operator-tunable; resolve via
        # ``ConfigResolver.get_float("security",
        # "timeout_check_interval_seconds")`` at the call site.
        if interval_seconds <= 0:
            msg = f"interval_seconds must be positive, got {interval_seconds}"
            raise ValueError(msg)
        self._store = approval_store
        self._checker = timeout_checker
        self._interval = interval_seconds
        self._on_resolve = on_timeout_resolve
        self._notification_dispatcher = notification_dispatcher
        self._task: asyncio.Task[None] | None = None
        self._background_tasks = BackgroundTaskRegistry(
            owner="security.timeout.scheduler",
        )
        # Loop-bound asyncio primitives are deferred until ``start()``
        # so the scheduler can be safely re-started on a different
        # event loop.  Eager construction in ``__init__`` would stick
        # them to whichever loop happened to be current at instantiation
        # time -- typically a problem in tests where pytest-asyncio
        # creates a fresh function-scoped loop per test while the
        # scheduler instance is held by a session-scoped Litestar app.
        # ``_lifecycle_lock`` still serialises concurrent start/stop on
        # the SAME loop; the running-loop check inside ``start()``
        # handles the cross-loop case before we ever try to acquire it.
        self._lifecycle_lock: asyncio.Lock | None = None
        self._wake_event: asyncio.Event | None = None
        self._stop_failed = False

    @property
    def is_running(self) -> bool:
        """Whether the scheduler loop is currently active.

        Returns ``False`` when the existing task is bound to a loop
        other than the currently-running one (e.g. test scenarios where
        the scheduler outlived its original loop).  In that case
        :meth:`start` will discard the stale task and spawn a fresh one.
        """
        if self._task is None or self._task.done():
            return False
        try:
            return self._task.get_loop() is asyncio.get_running_loop()
        except RuntimeError:
            return False

    def _drop_stale_loop_state(self) -> None:
        """Discard task/primitives bound to a closed-or-other event loop."""
        self._task = None
        self._wake_event = None
        self._lifecycle_lock = None

    async def start(self) -> None:
        """Start the background scheduler loop.

        Creates an ``asyncio.Task`` running ``_run_loop``.
        No-op if already running on the current loop.  When a previous
        ``start()`` ran on a different (now-closed) loop -- e.g. across
        pytest-asyncio's per-test loops -- the stale task and its
        loop-bound primitives are discarded and fresh ones are spawned
        on the current loop.

        Raises:
            RuntimeError: If a prior :meth:`stop` timed out and the
                scheduler is now unrestartable; construct a fresh
                instance instead.
        """
        # Detect cross-loop reuse before touching any lifecycle primitive.
        # Otherwise ``async with self._lifecycle_lock`` would itself raise
        # ``<Lock> is bound to a different event loop`` on the FIRST line
        # of the function and there is nothing the scheduler can do to
        # recover after that.
        if self._task is not None:
            current = asyncio.get_running_loop()
            try:
                same_loop = self._task.get_loop() is current
            except RuntimeError:
                same_loop = False
            if not same_loop:
                self._drop_stale_loop_state()
        if self._lifecycle_lock is None:
            self._lifecycle_lock = asyncio.Lock()
        if self._wake_event is None:
            self._wake_event = asyncio.Event()
        async with self._lifecycle_lock:
            if self._stop_failed:
                msg = (
                    "ApprovalTimeoutScheduler is unrestartable after a "
                    "timed-out stop; construct a fresh instance."
                )
                raise RuntimeError(msg)
            if self.is_running:
                return
            self._wake_event.clear()
            self._task = asyncio.create_task(
                self._run_loop(),
                name="approval-timeout-scheduler",
            )
            logger.info(
                TIMEOUT_SCHEDULER_STARTED,
                interval_seconds=self._interval,
            )

    async def stop(self, *, timeout: float | None = None) -> None:  # noqa: ASYNC109
        """Cancel the background scheduler and wait for it to finish.

        Holds ``_lifecycle_lock`` across the full body so a racing
        ``start()`` cannot interleave between cancel and the
        ``self._task = None`` assignment.

        Args:
            timeout: Seconds to wait for cancellation + drain. ``None``
                means "wait indefinitely". Must be positive when set.

        Raises:
            ValueError: If ``timeout`` is non-positive.
            TimeoutError: If cancellation + drain do not complete within
                ``timeout``. The scheduler is marked unrestartable so
                a subsequent :meth:`start` raises ``RuntimeError``;
                operators must construct a fresh instance because the
                prior task may still be in flight finishing its cleanup
                and a new task spawned alongside it would break the
                single-writer invariant.
        """
        if timeout is not None and timeout <= 0:
            msg = f"stop() timeout must be > 0, got {timeout!r}"
            raise ValueError(msg)
        if self._lifecycle_lock is None:
            # Scheduler was never started; nothing to stop.
            await self._background_tasks.drain()
            return
        async with self._lifecycle_lock:
            if self._task is None:
                await self._background_tasks.drain()
                return
            try:
                if timeout is None:
                    await self._cancel_and_drain()
                else:
                    await asyncio.wait_for(
                        self._cancel_and_drain(),
                        timeout=timeout,
                    )
            except TimeoutError:
                self._stop_failed = True
                logger.error(  # noqa: TRY400
                    TIMEOUT_SCHEDULER_ERROR,
                    error="stop drain timed out",
                    timeout_seconds=timeout,
                )
                raise
            self._task = None
            logger.info(TIMEOUT_SCHEDULER_STOPPED)

    async def _cancel_and_drain(self) -> None:
        """Cancel the scheduler task and drain background callbacks."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._background_tasks.drain()

    def reschedule(self, interval_seconds: float) -> None:
        """Update the interval and interrupt the current sleep.

        The new interval takes effect immediately by waking the
        sleeping loop.

        Args:
            interval_seconds: New interval in seconds (must be > 0).

        Raises:
            ValueError: If interval_seconds is not positive.
        """
        if interval_seconds <= 0:
            msg = f"interval_seconds must be positive, got {interval_seconds}"
            raise ValueError(msg)
        self._interval = interval_seconds
        if self._wake_event is not None:
            # Scheduler is started; wake the loop so the new interval
            # takes effect immediately.  When not started yet, the
            # interval update lands on the next ``start()``.
            self._wake_event.set()
        logger.info(
            TIMEOUT_SCHEDULER_RESCHEDULED,
            interval_seconds=interval_seconds,
        )

    async def _run_loop(self) -> None:
        """Sleep-and-check loop.

        Logs and suppresses errors except ``MemoryError`` and
        ``RecursionError``.  ``self._wake_event`` is always non-None
        here because ``start()`` initialises it before spawning the
        task that drives this coroutine.
        """
        wake_event = self._wake_event
        if wake_event is None:  # defensive; start() guarantees non-None
            msg = "_run_loop invoked without an initialised wake event"
            raise RuntimeError(msg)
        while True:
            wake_event.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    wake_event.wait(),
                    timeout=self._interval,
                )
            logger.debug(TIMEOUT_SCHEDULER_TICK)
            try:
                await self._check_pending_approvals()
            except MemoryError, RecursionError:
                raise
            except Exception:
                logger.error(
                    TIMEOUT_SCHEDULER_ERROR,
                    error="Unexpected error in scheduler loop",
                    exc_info=True,
                )

    async def _check_pending_approvals(self) -> None:
        """Poll PENDING items and apply timeout policy."""
        try:
            items = await self._store.list_items(
                status=ApprovalStatus.PENDING,
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.error(
                TIMEOUT_SCHEDULER_ERROR,
                error="Failed to list pending approvals",
                exc_info=True,
            )
            return

        for item in items:
            await self._evaluate_item(item)

    async def _evaluate_item(self, item: ApprovalItem) -> None:
        """Evaluate a single item and apply the action if decisive."""
        try:
            updated, action = await self._checker.check_and_resolve(item)
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                TIMEOUT_SCHEDULER_ERROR,
                approval_id=item.id,
                error="Failed to evaluate item",
                exc_info=True,
            )
            return

        if action.action == TimeoutActionType.WAIT:
            return

        if action.action in {TimeoutActionType.APPROVE, TimeoutActionType.DENY}:
            await self._resolve_item(updated, action)
        elif action.action == TimeoutActionType.ESCALATE:
            logger.info(
                TIMEOUT_SCHEDULER_RESOLVED,
                approval_id=item.id,
                action=action.action.value,
                escalate_to=action.escalate_to,
                reason=action.reason,
            )
            self._background_tasks.spawn(
                self._notify_escalation(item, action),
                event=NOTIFICATION_ESCALATION_SEND,
                approval_id=item.id,
                escalate_to=action.escalate_to,
            )

    async def _resolve_item(
        self,
        item: ApprovalItem,
        action: TimeoutAction,
    ) -> None:
        """Persist an APPROVE/DENY resolution and invoke callback."""
        try:
            saved = await self._store.save_if_pending(item)
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.error(
                TIMEOUT_SCHEDULER_ERROR,
                approval_id=item.id,
                error="Failed to persist timeout resolution",
                exc_info=True,
            )
            return

        if saved is None:
            # Already decided concurrently -- nothing to do.
            return

        logger.info(
            TIMEOUT_SCHEDULER_RESOLVED,
            approval_id=item.id,
            action=action.action.value,
            reason=action.reason,
        )

        if self._on_resolve is not None:
            try:
                await self._on_resolve(saved, action)
            except MemoryError, RecursionError:
                raise
            except Exception:
                logger.error(
                    TIMEOUT_SCHEDULER_ERROR,
                    approval_id=item.id,
                    error="on_timeout_resolve callback failed",
                    exc_info=True,
                )

    async def _notify_escalation(
        self,
        item: ApprovalItem,
        action: TimeoutAction,
    ) -> None:
        """Dispatch an escalation notification.

        Runs inside :class:`BackgroundTaskRegistry` so any exception
        is captured by the registry's done-callback and logged as
        ``NOTIFICATION_SEND_FAILED``. The previous version wrapped
        the dispatch call in a broad ``try/except`` that swallowed
        exceptions; that defeated the registry's failure-visibility
        guarantee (issue #1404) and was the whole reason
        notifications were moved behind the registry in the first
        place.
        """
        if self._notification_dispatcher is None:
            return
        from synthorg.notifications.models import (  # noqa: PLC0415
            Notification,
            NotificationCategory,
            NotificationSeverity,
        )

        await self._notification_dispatcher.dispatch(
            Notification(
                category=NotificationCategory.SECURITY,
                severity=NotificationSeverity.WARNING,
                title=f"Approval escalated: {item.id}",
                body=action.reason or "",
                source="security.timeout.scheduler",
                metadata={
                    "approval_id": item.id,
                    "escalate_to": action.escalate_to,
                },
            ),
        )
