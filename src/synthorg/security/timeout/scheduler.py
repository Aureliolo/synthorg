"""Approval timeout scheduler -- periodic background approval checking.

Polls the ``ApprovalStore`` for PENDING items and evaluates each
against the configured ``TimeoutPolicy`` via ``TimeoutChecker``.
When an item times out, the scheduler applies the policy action
(approve, deny, or escalate) and invokes an optional callback
for downstream resume/review-gate logic.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from synthorg.approval.enums import ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.actor_context import ActorIdentity, actor_scope
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.observability import get_logger
from synthorg.observability.background_tasks import BackgroundTaskRegistry
from synthorg.observability.events.approval_gate import APPROVAL_STATUS_TRANSITIONED
from synthorg.observability.events.notification import NOTIFICATION_ESCALATION_SEND
from synthorg.observability.events.timeout import (
    TIMEOUT_SCHEDULER_ERROR,
    TIMEOUT_SCHEDULER_RESCHEDULED,
    TIMEOUT_SCHEDULER_RESOLVED,
    TIMEOUT_SCHEDULER_STARTED,
    TIMEOUT_SCHEDULER_STOPPED,
    TIMEOUT_SCHEDULER_TICK,
)
from synthorg.security.timeout.enums import TimeoutActionType
from synthorg.security.timeout.models import TimeoutAction
from synthorg.security.timeout.timeout_checker import (
    TIMEOUT_POLICY_DECIDER,
    TimeoutChecker,
)

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
        """Whether the scheduler loop is currently active."""
        return self._task is not None and not self._task.done()

    def _task_is_on_current_loop(self) -> bool:
        """True iff the existing task is alive on the current loop.

        Used internally by ``start()`` to detect cross-loop reuse.
        Distinct from :attr:`is_running` so the public predicate stays
        cheap and mockable in tests; loop introspection only matters
        on the cross-loop-restart path.

        Returns ``True`` (i.e. "do not drop state") when the task or
        loop cannot be introspected -- typically a ``MagicMock(spec=
        asyncio.Task)`` in tests where ``get_loop()`` returns a mock.
        Erring on the side of "same loop" prevents spurious task drops
        in unit tests; the genuine cross-loop scenario in production
        always returns a real ``AbstractEventLoop``.

        Returns:
            ``True`` when the existing task is alive on the current loop
            (or cannot be introspected); ``False`` otherwise.
        """
        if self._task is None or self._task.done():
            return False
        try:
            # ``object`` annotation defeats mypy's narrowing of
            # ``Task.get_loop`` so the runtime ``isinstance`` check
            # below is reachable for ``MagicMock(spec=Task)`` test
            # fixtures whose ``get_loop`` returns a mock value.
            task_loop: object = self._task.get_loop()
        except RuntimeError, AttributeError:
            return True
        if not isinstance(task_loop, asyncio.AbstractEventLoop):
            return True
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            return True
        return task_loop is current

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
        if self._task is not None and not self._task_is_on_current_loop():
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
                logger.error(
                    TIMEOUT_SCHEDULER_ERROR,
                    error="stop drain timed out",
                    timeout_seconds=timeout,
                )
                raise
            self._task = None
            # Drop loop-bound primitives so a subsequent ``start()`` on
            # a different event loop reconstructs them. Without this,
            # ``_task_is_on_current_loop()`` is skipped (``_task`` is
            # already ``None``) and the next ``start()`` reuses the
            # wake event / lifecycle lock from the now-dead loop.
            self._wake_event = None
            self._lifecycle_lock = None
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

        Raises:
            RuntimeError: If invoked before ``start()`` initialised the
                wake event (defensive; should not happen in practice).
        """
        wake_event = self._wake_event
        if wake_event is None:  # defensive; start() guarantees non-None
            msg = "_run_loop invoked without an initialised wake event"
            raise RuntimeError(msg)
        # lint-allow: long-running-loop-kill-switch -- stop()/cancel drives shutdown.
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
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.error(
                    TIMEOUT_SCHEDULER_ERROR,
                    error="Unexpected error in scheduler loop",
                )

    async def _check_pending_approvals(self) -> None:
        """Poll PENDING items and apply timeout policy."""
        try:
            items = await self._store.list_items(
                status=ApprovalStatus.PENDING,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.error(
                TIMEOUT_SCHEDULER_ERROR,
                error="Failed to list pending approvals",
            )
            return

        for item in items:
            await self._evaluate_item(item)

    async def _evaluate_item(self, item: ApprovalItem) -> None:
        """Evaluate a single item and apply the action if decisive.

        RFC#3 / ADR-0003: this is a system-initiated decision path with
        no human in the loop. The system actor is bound for the whole
        evaluation so any downstream gate / resume flow resolves
        ``decided_by`` to the timeout-policy identity via the actor
        seam -- byte-identical to the value the timeout checker writes
        directly into the approval row.
        """
        with actor_scope(ActorIdentity.system(TIMEOUT_POLICY_DECIDER)):
            try:
                updated, action = await self._checker.check_and_resolve(item)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    TIMEOUT_SCHEDULER_ERROR,
                    approval_id=str(item.id),
                    error="Failed to evaluate item",
                )
                return

            if action.action == TimeoutActionType.WAIT:
                return

            if action.action in {
                TimeoutActionType.APPROVE,
                TimeoutActionType.DENY,
            }:
                await self._resolve_item(updated, action)
            elif action.action == TimeoutActionType.ESCALATE:
                logger.info(
                    TIMEOUT_SCHEDULER_RESOLVED,
                    approval_id=str(item.id),
                    action=action.action.value,
                    escalate_to=action.escalate_to,
                    reason=action.reason,
                )
                _ = self._background_tasks.spawn(
                    self._notify_escalation(item, action),
                    event=NOTIFICATION_ESCALATION_SEND,
                    approval_id=str(item.id),
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.error(
                TIMEOUT_SCHEDULER_ERROR,
                approval_id=str(item.id),
                error="Failed to persist timeout resolution",
            )
            return

        if saved is None:
            # Already decided concurrently -- nothing to do.
            return

        logger.info(
            TIMEOUT_SCHEDULER_RESOLVED,
            approval_id=str(item.id),
            action=action.action.value,
            reason=action.reason,
        )
        # State-transition log AFTER the persistence write succeeds, so the
        # PENDING -> APPROVED / REJECTED hop appears in the
        # ``approval.status_transitioned`` stream like every other approval
        # decision, attributed to the timeout policy rather than a reviewer.
        logger.info(
            APPROVAL_STATUS_TRANSITIONED,
            approval_id=str(item.id),
            from_status=ApprovalStatus.PENDING.value,
            to_status=saved.status.value,
            decided_by=TIMEOUT_POLICY_DECIDER,
        )

        if self._on_resolve is not None:
            try:
                await self._on_resolve(saved, action)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.error(
                    TIMEOUT_SCHEDULER_ERROR,
                    approval_id=str(item.id),
                    error="on_timeout_resolve callback failed",
                )

    async def _notify_escalation(
        self,
        item: ApprovalItem,
        action: TimeoutAction,
    ) -> None:
        """Dispatch an escalation notification.

        Runs inside :class:`BackgroundTaskRegistry` so any exception
        is captured by the registry's done-callback and logged as
        ``NOTIFICATION_SEND_FAILED``. The dispatch call must NOT be
        wrapped in a broad ``try/except`` that swallows exceptions:
        the registry's failure-visibility guarantee depends on the
        coroutine raising naturally so the done-callback can record
        the failure.
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
                    "approval_id": str(item.id),
                    "escalate_to": action.escalate_to,
                },
            ),
        )
