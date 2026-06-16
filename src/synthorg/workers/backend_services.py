"""Backend-side distributed-path lifecycle bundle.

Three components run inside the backend process whenever
``config.queue.enabled`` is true and need the connected
:class:`JetStreamTaskQueue`:

- :class:`DeadLetterConsumer` -- drains the dead subject and fails
  exhausted tasks (closes the no-loss gap).
- :class:`SeenClaimsPruner` -- bounds the dedup table.
- :class:`WorkerHeartbeatSubscriber` -- surfaces worker liveness in
  the structured-log pipeline.

They share one start/stop seam so the API lifecycle manages exactly
one extra handle (mirroring the existing ``distributed_task_queue``
seam) rather than threading three through ``AppState``. Start order:
pruner (independent), then the two NATS consumers (need the queue
connected). Stop order is reversed and best-effort so one slow
component cannot strand the others.
"""

import asyncio
from typing import TYPE_CHECKING, Protocol

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workers import (
    WORKERS_BACKEND_BUNDLE_START_FAILED,
    WORKERS_BACKEND_BUNDLE_STARTED,
    WORKERS_BACKEND_BUNDLE_STOP_FAILED,
)
from synthorg.persistence.seen_claims_protocol import SeenClaimsRepository
from synthorg.workers.config import QueueConfig
from synthorg.workers.dead_letter import (
    DeadLetterConsumer,
    make_engine_task_fail_handler,
)
from synthorg.workers.heartbeat_subscriber import WorkerHeartbeatSubscriber
from synthorg.workers.seen_claims_pruner import SeenClaimsPruner

if TYPE_CHECKING:
    # TaskEngine is named for signatures only: the engine package must
    # not load when the distributed path is unused, and tests inject
    # duck-typed engine fakes.
    from synthorg.engine.task_engine import TaskEngine

    # Concrete-faked collaborator: tests inject duck-typed queue stubs,
    # so a runtime import would make typeguard reject the fakes.
    from synthorg.workers.claim import JetStreamTaskQueue

logger = get_logger(__name__)


class _LifecycleComponent(Protocol):
    """Structural type the bundled components satisfy."""

    @property
    def is_running(self) -> bool: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class DistributedBackendServices:
    """Composite start/stop for the backend distributed-path services."""

    def __init__(
        self,
        *,
        dead_letter: DeadLetterConsumer,
        pruner: SeenClaimsPruner,
        heartbeat: WorkerHeartbeatSubscriber,
    ) -> None:
        self._dead_letter = dead_letter
        self._pruner = pruner
        self._heartbeat = heartbeat
        # Serialises the composite check-and-set on the bundle's running
        # state: without it a racing lifecycle re-entry could double-start
        # every sub-service or interleave a stop() mid-start().
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- ctx
        # Start order (stop is the reverse); each entry is
        # (name, component). Pruner first (no NATS dependency), then the
        # two consumers that need the connected queue.
        self._start_order: tuple[tuple[str, _LifecycleComponent], ...] = (
            ("seen_claims_pruner", self._pruner),
            ("dead_letter_consumer", self._dead_letter),
            ("heartbeat_subscriber", self._heartbeat),
        )

    @property
    def is_running(self) -> bool:
        """True when every bundled component is running."""
        return (
            self._pruner.is_running
            and self._dead_letter.is_running
            and self._heartbeat.is_running
        )

    async def start(self) -> None:
        """Start every component; roll back already-started ones on failure.

        Called by the API lifecycle immediately after the distributed
        task queue connects. A start failure here must not leave a
        half-started bundle behind, so already-started components are
        stopped before the error propagates. The lifecycle lock
        serialises the whole sequence so a racing ``start()`` / ``stop()``
        cannot interleave with this fan-out.
        """
        async with self._lifecycle_lock:
            if self.is_running:
                # Already fully started: a second start() queued behind the
                # lock would re-call each component's non-idempotent start()
                # and duplicate subscribers/pruners. Make start() idempotent.
                return
            started: list[tuple[str, _LifecycleComponent]] = []
            failed_component = "<unknown>"
            try:
                for name, component in self._start_order:
                    failed_component = name
                    await component.start()
                    started.append((name, component))
            except Exception as exc:
                # Critical errors skip the rollback: stopping components is
                # async teardown work that may allocate, and must not run
                # under catastrophic interpreter state.
                reraise_critical(exc)
                logger.error(
                    WORKERS_BACKEND_BUNDLE_START_FAILED,
                    component=failed_component,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                for _name, component in reversed(started):
                    try:
                        await component.stop()
                    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                        reraise_critical(exc)
                        logger.warning(
                            WORKERS_BACKEND_BUNDLE_STOP_FAILED,
                            component=_name,
                            error_type=type(exc).__name__,
                            error=safe_error_description(exc),
                        )
                raise
            logger.info(WORKERS_BACKEND_BUNDLE_STARTED)

    async def stop(self) -> None:
        """Stop every component, reverse order, best-effort. Idempotent."""
        async with self._lifecycle_lock:
            for name, component in reversed(self._start_order):
                try:
                    await component.stop()
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    logger.warning(
                        WORKERS_BACKEND_BUNDLE_STOP_FAILED,
                        component=name,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )


def build_distributed_backend_services(
    *,
    task_queue: JetStreamTaskQueue,
    engine: TaskEngine,
    queue_config: QueueConfig,
    seen_claims: SeenClaimsRepository,
    clock: Clock | None = None,
) -> DistributedBackendServices:
    """Construct the backend distributed-path service bundle.

    Args:
        task_queue: The (not-yet-started) distributed task queue; its
            NATS connection carries dead-letter + heartbeat traffic.
        engine: ``TaskEngine`` used to fail dead-lettered tasks through
            the single-writer mutation queue.
        queue_config: Effective queue configuration.
        seen_claims: Durable dedup repository (dead-letter dedup +
            pruning target).
        clock: Optional clock seam forwarded to every component.

    Returns:
        A ``DistributedBackendServices`` bundle wiring the dead-letter
        consumer, seen-claims pruner, and heartbeat subscriber.
    """
    dead_letter = DeadLetterConsumer(
        task_queue=task_queue,
        task_fail_handler=make_engine_task_fail_handler(engine),
        queue_config=queue_config,
        seen_claims=seen_claims,
        clock=clock,
    )
    pruner = SeenClaimsPruner(
        seen_claims=seen_claims,
        interval_seconds=float(queue_config.prune_interval_seconds),
        clock=clock,
    )
    heartbeat = WorkerHeartbeatSubscriber(
        task_queue=task_queue,
        queue_config=queue_config,
        clock=clock,
    )
    return DistributedBackendServices(
        dead_letter=dead_letter,
        pruner=pruner,
        heartbeat=heartbeat,
    )
