"""Backend-side worker-liveness heartbeat subscriber.

Workers publish an at-most-once :class:`WorkerHeartbeat` on
``synthorg.workers.heartbeat.<worker_id>`` (core NATS). This subscriber
observes those beats and surfaces liveness through the structured-log
pipeline (the project's primary observability surface): an
``WORKERS_HEARTBEAT_OBSERVED`` INFO per beat and a
``WORKERS_HEARTBEAT_STALE`` WARNING when a previously-seen worker stops
beating.

Correctness of crash recovery does NOT depend on this: a crashed
worker's in-flight claim is redelivered by JetStream ``ack_wait``. The
subscriber is purely operator visibility, so a missed beat or a brief
broker blip degrades to "no signal", never to task loss.
"""

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any, Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workers import (
    WORKERS_HEARTBEAT_OBSERVED,
    WORKERS_HEARTBEAT_STALE,
    WORKERS_HEARTBEAT_SUBSCRIBER_FAILED,
    WORKERS_HEARTBEAT_SUBSCRIBER_STARTED,
    WORKERS_HEARTBEAT_SUBSCRIBER_STOPPED,
)
from synthorg.workers.config import QueueConfig  # noqa: TC001
from synthorg.workers.heartbeat_models import (
    HEARTBEAT_SUBJECT_PREFIX,
    WorkerHeartbeat,
)

if TYPE_CHECKING:
    from synthorg.workers.claim import JetStreamTaskQueue

logger = get_logger(__name__)

_STALE_INTERVAL_FACTOR: Final[float] = 3.0
"""A worker is stale once it misses this many heartbeat intervals.

Three intervals tolerates one or two dropped at-most-once beats (or a
brief broker reconnect) before declaring the worker stale, so a single
lost packet does not produce a spurious WARNING."""

_SWEEP_SUBJECT: Final[str] = f"{HEARTBEAT_SUBJECT_PREFIX}.>"
"""Wildcard subject matching every worker's heartbeat leaf."""


class WorkerHeartbeatSubscriber:
    """Observes worker heartbeats and logs liveness + staleness.

    Args:
        task_queue: Connected :class:`JetStreamTaskQueue` (its core
            NATS connection carries the heartbeats).
        queue_config: Queue config; ``heartbeat_interval_seconds``
            drives the staleness threshold and sweep cadence.
        clock: Clock seam; ``FakeClock`` drives staleness in tests.
    """

    def __init__(
        self,
        *,
        task_queue: JetStreamTaskQueue,
        queue_config: QueueConfig,
        clock: Clock | None = None,
    ) -> None:
        self._task_queue = task_queue
        self._clock: Clock = clock or SystemClock()
        self._interval = float(queue_config.heartbeat_interval_seconds)
        self._stale_after = self._interval * _STALE_INTERVAL_FACTOR
        # worker_id -> monotonic-ish wall seconds of last observed beat.
        self._last_seen: dict[str, float] = {}
        # Workers currently flagged stale, so the WARNING fires once on
        # the alive->stale edge rather than every sweep.
        self._flagged_stale: set[str] = set()
        self._running = False
        self._stop_event = asyncio.Event()  # lint-allow: loop-bound-init -- see Worker
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- ctx
        self._subscription: Any = None
        self._sweep_task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        """Whether the subscriber is active."""
        return self._running

    async def start(self) -> None:
        """Subscribe to the heartbeat subject and start the stale sweep.

        Raises:
            RuntimeError: If already running.
        """
        async with self._lifecycle_lock:
            if self._running:
                msg = "WorkerHeartbeatSubscriber is already running"
                raise RuntimeError(msg)
            self._subscription = await self._task_queue.core_subscribe(
                _SWEEP_SUBJECT,
                self._on_message,
            )
            self._running = True
            self._stop_event.clear()
            self._sweep_task = asyncio.create_task(self._sweep_loop())
            logger.info(WORKERS_HEARTBEAT_SUBSCRIBER_STARTED)

    async def stop(self) -> None:
        """Unsubscribe and stop the sweep loop. Idempotent."""
        async with self._lifecycle_lock:
            if not self._running:
                return
            self._stop_event.set()
            subscription = self._subscription
            sweep = self._sweep_task
        if subscription is not None:
            with contextlib.suppress(Exception):
                await subscription.unsubscribe()
        if sweep is not None:
            sweep.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweep
        async with self._lifecycle_lock:
            self._running = False
            self._subscription = None
            self._sweep_task = None
            logger.info(WORKERS_HEARTBEAT_SUBSCRIBER_STOPPED)

    async def _on_message(self, msg: Any) -> None:
        """Record one observed beat. Malformed payloads are dropped."""
        try:
            beat = WorkerHeartbeat.model_validate_json(
                msg.data.decode("utf-8"),
            )
        except (ValueError, UnicodeDecodeError) as exc:
            logger.warning(
                WORKERS_HEARTBEAT_SUBSCRIBER_FAILED,
                reason="parse_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return
        worker_id = str(beat.worker_id)
        self._last_seen[worker_id] = self._now_seconds()
        self._flagged_stale.discard(worker_id)
        logger.info(
            WORKERS_HEARTBEAT_OBSERVED,
            worker_id=worker_id,
            claims_done=beat.claims_done,
        )

    async def _sweep_loop(self) -> None:
        """Flag workers that have stopped beating, once per transition."""
        # lint-allow: long-running-loop-kill-switch -- _stop_event drives shutdown.
        while not self._stop_event.is_set():
            await self._clock.sleep(self._interval)
            if self._stop_event.is_set():
                return
            self._sweep_once()

    def _sweep_once(self) -> None:
        """One staleness pass over all observed workers."""
        now = self._now_seconds()
        for worker_id, last in self._last_seen.items():
            if worker_id in self._flagged_stale:
                continue
            age = now - last
            if age > self._stale_after:
                self._flagged_stale.add(worker_id)
                logger.warning(
                    WORKERS_HEARTBEAT_STALE,
                    worker_id=worker_id,
                    age_seconds=round(age, 3),
                    stale_after_seconds=self._stale_after,
                )

    def _now_seconds(self) -> float:
        """Wall-clock seconds from the injected clock (test-seam-safe)."""
        return self._clock.now().timestamp()
