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

from typing import TYPE_CHECKING, Protocol

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workers import (
    WORKERS_BACKEND_BUNDLE_STARTED,
    WORKERS_BACKEND_BUNDLE_STOP_FAILED,
)
from synthorg.workers.dead_letter import (
    DeadLetterConsumer,
    make_engine_task_fail_handler,
)
from synthorg.workers.heartbeat_subscriber import WorkerHeartbeatSubscriber
from synthorg.workers.seen_claims_pruner import SeenClaimsPruner

if TYPE_CHECKING:
    from typing import Any

    from synthorg.core.clock import Clock
    from synthorg.persistence.seen_claims_protocol import SeenClaimsRepository
    from synthorg.workers.claim import JetStreamTaskQueue
    from synthorg.workers.config import QueueConfig

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
        stopped before the error propagates.
        """
        started: list[tuple[str, _LifecycleComponent]] = []
        try:
            for name, component in self._start_order:
                await component.start()
                started.append((name, component))
        except Exception:
            for _name, component in reversed(started):
                try:
                    await component.stop()
                except Exception as exc:
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
        for name, component in reversed(self._start_order):
            try:
                await component.stop()
            except Exception as exc:
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
    engine: Any,
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
