"""Distributed dispatcher: observes task state changes, enqueues claims.

Registered with :meth:`TaskEngine.register_observer` at engine startup
when ``config.queue.enabled`` is true. The dispatcher is a passive
observer: it never mutates task state directly. Instead it publishes
claim messages to the JetStream work queue, and workers pull from
there to execute tasks.

Single-writer invariant: the dispatcher does not write task state.
Workers call the backend HTTP API to transition tasks, which routes
through the normal ``TaskEngine`` mutation queue. The dispatcher only
reacts to successful mutations and publishes the enqueue signal.
"""

from typing import TYPE_CHECKING, Final

from synthorg.core.resilience import GeneralRetryHandler
from synthorg.observability import get_logger
from synthorg.observability.events.workers import (
    WORKERS_DISPATCHER_CLAIM_ENQUEUED,
    WORKERS_DISPATCHER_PUBLISH_EXHAUSTED,
    WORKERS_DISPATCHER_PUBLISH_FAILED,
    WORKERS_DISPATCHER_PUBLISH_RETRYING,
    WORKERS_DISPATCHER_QUEUE_NOT_RUNNING,
)
from synthorg.workers.claim import TaskClaim

if TYPE_CHECKING:
    from synthorg.engine.task_engine_models import TaskStateChanged
    from synthorg.workers.claim import JetStreamTaskQueue

logger = get_logger(__name__)

_DISPATCHABLE_TRANSITIONS: frozenset[str] = frozenset(
    {
        "assigned",
    },
)
"""Task statuses that trigger a claim enqueue.

The dispatcher fires when a task transitions *into* one of these
statuses. ``ASSIGNED`` is the "ready to run" state per the task
engine lifecycle (``CREATED -> ASSIGNED -> IN_PROGRESS``): a worker
picks up an assigned task, transitions it to ``IN_PROGRESS``, and
executes. Adding ``IN_PROGRESS`` here would cause double dispatch,
so it is deliberately omitted.

Values are matched case-insensitively against ``TaskStatus.value``.
"""

_PUBLISH_MAX_ATTEMPTS: Final[int] = 3
"""Max publish attempts per claim before giving up.

A transient NATS hiccup (reconnect, brief server unavailability)
should not orphan a task in ``ASSIGNED`` status. We retry publishes
up to this many times before logging an exhaustion event and
returning. The dispatcher cannot roll the task back itself without
breaking the single-writer invariant -- workers are the only
component allowed to transition tasks through the HTTP API -- so
once retries are exhausted we emit a structured error that
operators can observe and act on. Tasks left in ``ASSIGNED`` will
eventually be picked up again the next time the engine replays
observer events (e.g., on engine restart).
"""

_PUBLISH_BACKOFF_BASE_SECONDS: Final[float] = 0.1
"""Base delay for exponential backoff between publish retries."""

_PUBLISH_BACKOFF_CAP_SECONDS: Final[float] = 1.0
"""Upper bound on a single inter-attempt delay.

With ``base=0.1`` and ``max_attempts=3`` the unbounded delays are
``0.1`` and ``0.2`` seconds, well under the cap.  The cap exists
defensively so a future bump to ``max_attempts`` does not silently
push the publish path into multi-second sleeps.
"""


class DistributedDispatcher:
    """Observer that publishes task claims to the JetStream work queue.

    Args:
        task_queue: Connected :class:`JetStreamTaskQueue`.

    The dispatcher assumes the task queue is already started. Start
    it before registering the observer with the engine.
    """

    def __init__(self, *, task_queue: JetStreamTaskQueue) -> None:
        self._task_queue = task_queue
        self._retry = GeneralRetryHandler(
            retryable=lambda _exc: True,
            max_attempts=_PUBLISH_MAX_ATTEMPTS,
            base=_PUBLISH_BACKOFF_BASE_SECONDS,
            cap=_PUBLISH_BACKOFF_CAP_SECONDS,
            event=WORKERS_DISPATCHER_PUBLISH_RETRYING,
            jitter=False,
        )

    async def on_task_state_changed(
        self,
        event: TaskStateChanged,
    ) -> None:
        """Handle a :class:`TaskStateChanged` event from the engine.

        Filters events to dispatchable status transitions and enqueues
        a claim for each matching task.
        """
        if not self._is_dispatchable(event):
            return

        if not self._task_queue.is_running:
            logger.warning(
                WORKERS_DISPATCHER_QUEUE_NOT_RUNNING,
                task_id=event.task_id,
            )
            return

        claim = self._build_claim(event)
        if not await self._publish_with_retry(claim, event.task_id):
            return
        logger.info(
            WORKERS_DISPATCHER_CLAIM_ENQUEUED,
            task_id=event.task_id,
            new_status=claim.new_status,
        )

    async def _publish_with_retry(
        self,
        claim: TaskClaim,
        task_id: str,
    ) -> bool:
        """Publish a claim with bounded exponential backoff.

        Returns ``True`` on success and ``False`` once retries are
        exhausted. A failed publish can orphan a task in ``ASSIGNED``
        because the dispatcher is a passive observer and cannot roll
        task state back itself (workers are the only writers via the
        HTTP API). Retries cover transient NATS hiccups; persistent
        failures surface via ``WORKERS_DISPATCHER_PUBLISH_EXHAUSTED``
        so operators can re-drive the task through an engine replay.
        """

        async def publish() -> None:
            await self._task_queue.publish_claim(claim)

        try:
            await self._retry.execute(publish, task_id=task_id)
        except Exception as exc:
            # Preserve the original, less-severe event on the final
            # failure so downstream monitoring that still filters on
            # ``WORKERS_DISPATCHER_PUBLISH_FAILED`` does not silently
            # stop seeing these failures, and emit the new exhausted
            # event with the attempt count.  Carrying ``error_type``
            # lets operators tell transient retry-exhaustion apart
            # from permanent failures (auth, malformed config) that
            # the broad ``retryable=lambda _exc: True`` predicate
            # cannot distinguish on its own.
            logger.exception(
                WORKERS_DISPATCHER_PUBLISH_FAILED,
                task_id=task_id,
                error_type=type(exc).__name__,
            )
            logger.error(  # noqa: TRY400
                WORKERS_DISPATCHER_PUBLISH_EXHAUSTED,
                task_id=task_id,
                attempts=_PUBLISH_MAX_ATTEMPTS,
                error_type=type(exc).__name__,
            )
            return False
        else:
            return True

    @staticmethod
    def _is_dispatchable(event: TaskStateChanged) -> bool:
        """Return True if the event is a transition *into* a dispatchable status.

        Only fires when the task actually moves into one of the
        dispatchable statuses. Events that leave an already-assigned
        task in ``assigned`` (e.g., metadata edits, observer replays)
        are ignored so the same claim is never enqueued twice.
        """
        if event.new_status is None:
            return False
        new_value = str(event.new_status.value).lower()
        if new_value not in _DISPATCHABLE_TRANSITIONS:
            return False
        if event.previous_status is None:
            return True
        previous_value = str(event.previous_status.value).lower()
        return previous_value != new_value

    @staticmethod
    def _build_claim(event: TaskStateChanged) -> TaskClaim:
        """Build a :class:`TaskClaim` from a state-change event."""
        project_id: str | None = None
        if event.task is not None and event.task.project is not None:
            project_id = str(event.task.project)
        previous = None
        if event.previous_status is not None:
            previous = str(event.previous_status.value)
        return TaskClaim(
            task_id=event.task_id,
            project_id=project_id,
            previous_status=previous,
            new_status=str(event.new_status.value) if event.new_status else "unknown",
        )
