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

from typing import TYPE_CHECKING

from synthorg.core.resilience import GeneralRetryHandler
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workers import (
    WORKERS_DISPATCHER_CLAIM_ENQUEUED,
    WORKERS_DISPATCHER_PUBLISH_EXHAUSTED,
    WORKERS_DISPATCHER_PUBLISH_FAILED,
    WORKERS_DISPATCHER_PUBLISH_RETRYING,
    WORKERS_DISPATCHER_QUEUE_NOT_RUNNING,
)
from synthorg.settings.bridge_configs import WorkersBridgeConfig
from synthorg.workers.claim import TaskClaim

if TYPE_CHECKING:
    from collections.abc import Callable

    from synthorg.core.clock import Clock
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


def _default_workers_bridge() -> WorkersBridgeConfig:
    """Fail-safe provider used until the live snapshot is bound.

    Returns a config whose Field defaults equal the registered
    ``workers.dispatcher_publish_*`` defaults (max attempts 3, backoff
    base 0.1s, cap 1.0s). The dispatcher is constructed in
    :func:`synthorg.api.auto_wire.auto_wire_phase1` before ``AppState``
    exists, so until the startup hook late-binds the live provider this
    keeps the retry budget identical to the registered defaults rather
    than silently disabling retries on a settings-backend hiccup.

    Rationale for the retry budget: a transient NATS hiccup (reconnect,
    brief server unavailability) must not orphan a task in ``ASSIGNED``.
    The dispatcher cannot roll the task back itself without breaking the
    single-writer invariant -- workers are the only component allowed to
    transition tasks through the HTTP API -- so once retries are
    exhausted it emits a structured error operators can act on. Tasks
    left in ``ASSIGNED`` are re-picked the next time the engine replays
    observer events (e.g. on engine restart). The cap bounds a single
    inter-attempt delay so a future operator bump to ``max_attempts``
    cannot silently push the publish path into multi-second sleeps.
    """
    return WorkersBridgeConfig()


class DistributedDispatcher:
    """Observer that publishes task claims to the JetStream work queue.

    Args:
        task_queue: Connected :class:`JetStreamTaskQueue`.
        clock: Optional clock seam for deterministic retry backoff in
            tests; ``GeneralRetryHandler`` defaults to ``SystemClock``.
        workers_bridge_provider: Optional callable returning the live
            :class:`WorkersBridgeConfig` snapshot. Omitted at
            construction (the dispatcher is built before ``AppState``);
            the API startup hook late-binds the live provider via
            :meth:`set_workers_bridge_provider`.

    The dispatcher assumes the task queue is already started. Start
    it before registering the observer with the engine.
    """

    def __init__(
        self,
        *,
        task_queue: JetStreamTaskQueue,
        clock: Clock | None = None,
        workers_bridge_provider: (Callable[[], WorkersBridgeConfig] | None) = None,
    ) -> None:
        self._task_queue = task_queue
        self._clock = clock
        self._workers_bridge_provider: Callable[[], WorkersBridgeConfig] = (
            workers_bridge_provider
            if workers_bridge_provider is not None
            else _default_workers_bridge
        )

    def set_workers_bridge_provider(
        self,
        provider: Callable[[], WorkersBridgeConfig],
    ) -> None:
        """Late-bind the live bridge-config provider after AppState exists.

        Mirrors :meth:`OAuthTokenManager.set_config_resolver`: the
        dispatcher is instantiated in ``auto_wire_phase1`` before
        ``AppState``, so the API startup hook injects
        ``lambda: app_state.workers_bridge_config`` here. Each publish
        then reads the current snapshot, so an operator hot-reload of a
        ``workers.dispatcher_publish_*`` setting takes effect on the
        next publish without restarting the dispatcher.
        """
        self._workers_bridge_provider = provider

    def _build_retry(self) -> GeneralRetryHandler:
        """Build a retry handler from the current bridge snapshot.

        See docs/reference/retry-patterns.md: Pattern A -- transient
        I/O retry for the NATS publish hot path. Rebuilt per publish
        (a cheap object) so a hot-reloaded retry budget applies without
        subscriber-to-consumer plumbing, mirroring how controllers read
        ``app_state.api_bridge_config`` per request.
        """
        cfg = self._workers_bridge_provider()
        return GeneralRetryHandler(
            retryable=lambda _exc: True,
            max_attempts=cfg.dispatcher_publish_max_attempts,
            base=cfg.dispatcher_publish_backoff_base_seconds,
            cap=cfg.dispatcher_publish_backoff_cap_seconds,
            event=WORKERS_DISPATCHER_PUBLISH_RETRYING,
            jitter=False,
            clock=self._clock,
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

        retry = self._build_retry()
        max_attempts = retry.max_attempts
        try:
            await retry.execute(publish, task_id=task_id)
        except MemoryError, RecursionError:
            # System-fatal exceptions must propagate so the process can
            # crash deliberately rather than silently turning into a
            # ``False`` retry-exhaustion result that hides the cause.
            raise
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
            #
            # ``GeneralRetryHandler.execute`` may raise
            # ``RetryExhaustedError`` after the last attempt; in that
            # case unwrap to the underlying cause so ``error_type``
            # carries the actual publish failure (auth, timeout, etc.)
            # rather than the generic exhaustion wrapper.
            root_exc = exc.__cause__ or exc
            logger.warning(
                WORKERS_DISPATCHER_PUBLISH_FAILED,
                task_id=task_id,
                error_type=type(root_exc).__name__,
                error=safe_error_description(root_exc),
            )
            logger.error(
                WORKERS_DISPATCHER_PUBLISH_EXHAUSTED,
                task_id=task_id,
                attempts=max_attempts,
                error_type=type(root_exc).__name__,
                error=safe_error_description(root_exc),
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
