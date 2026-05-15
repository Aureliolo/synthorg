"""Task claim data model and JetStream work-queue client.

A :class:`TaskClaim` is the message body enqueued by the dispatcher
and consumed by workers. The claim identifies the task plus enough
provenance for workers to transition it correctly.

The :class:`JetStreamTaskQueue` wraps the raw ``nats-py`` JetStream
API behind a small ``publish_claim`` / ``next_claim`` / ``ack`` /
``nack`` surface that both the dispatcher and worker use.
"""

import asyncio
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.communication.bus import redact_url
from synthorg.communication.bus.errors import (
    BusConnectionError,
    BusStopTimeoutError,
    BusStreamError,
    BusUnrestartableError,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workers import (
    WORKERS_QUEUE_NOT_RUNNING,
    WORKERS_QUEUE_START_REJECTED,
    WORKERS_TASK_QUEUE_ACK_MALFORMED_FAILED,
    WORKERS_TASK_QUEUE_CLAIM_PARSE_FAILED,
    WORKERS_TASK_QUEUE_CONNECT_FAILED,
    WORKERS_TASK_QUEUE_DRAIN_FAILED,
    WORKERS_TASK_QUEUE_UNSUBSCRIBE_FAILED,
)
from synthorg.workers.config import QueueConfig  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.communication.config import NatsConfig

logger = get_logger(__name__)

_MAX_CLAIM_PAYLOAD_BYTES: Final[int] = 1 * 1024 * 1024
"""Maximum claim payload size (1 MB) accepted from the work queue.

Claims are small JSON envelopes; anything larger is either a protocol
mismatch or a DoS vector. Reject before invoking Pydantic validation
to prevent memory exhaustion on malformed claims.
"""

_STOP_DRAIN_TIMEOUT_SECONDS: Final[float] = 10.0
"""Hard deadline on ``JetStreamTaskQueue.stop()``'s NATS drain.

Per ``docs/reference/lifecycle-sync.md``, a stop that cannot drain
within a bounded window must mark the queue unrestartable rather than
hang forever. 10 seconds covers the routine ``client.drain()`` ack
flush; a longer wait usually means the NATS server is unreachable and
the operator should treat the queue as poisoned.
"""


class TaskClaimStatus(StrEnum):
    """Terminal worker outcome communicated back via HTTP + ack/nack."""

    SUCCESS = "success"
    FAILED = "failed"
    RETRY = "retry"


class TaskClaim(BaseModel):
    """Work item enqueued by the dispatcher, consumed by a worker.

    Attributes:
        task_id: ID of the task to execute.
        project_id: Project the task belongs to (for correlation).
        previous_status: Task status before the transition that made
            it ready to run.
        new_status: The status that caused the dispatch (typically
            ``READY`` or equivalent).
        dispatched_at: When the dispatcher enqueued this claim.
        idempotency_key: UUID generated at publish time so the worker
            can dedupe a JetStream redelivery (ack lost in transit,
            worker crash before ack). Workers ``mark_seen`` this key
            via :class:`SeenClaimsRepository` and short-circuit any
            duplicate observation back to ack-and-skip.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    task_id: NotBlankStr = Field(description="Task identifier")
    project_id: NotBlankStr | None = Field(
        default=None,
        description="Project identifier for correlation",
    )
    previous_status: str | None = Field(
        default=None,
        description="Task status before the triggering transition",
    )
    new_status: NotBlankStr = Field(
        description="Task status that triggered the dispatch",
    )
    dispatched_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the dispatcher enqueued the claim",
    )
    idempotency_key: NotBlankStr = Field(
        default_factory=lambda: uuid4().hex,
        description="Per-dispatch UUID for worker-side redelivery dedup",
    )


class JetStreamTaskQueue:
    """NATS JetStream work-queue client for distributed task claims.

    Creates a ``WorkQueuePolicy`` stream that holds claim messages
    exclusively (not shared with the message bus stream). Workers pull
    from a single durable consumer (configurable per deployment) and
    manually ack or nack each claim.

    Args:
        queue_config: Queue config (stream name, subjects, ack wait,
            max deliver).
        nats_config: Shared NATS connection settings (URL, creds,
            reconnect timing).
        durable_name: Durable consumer name used by workers. Defaults
            to ``"synthorg_workers"``.
    """

    def __init__(
        self,
        *,
        queue_config: QueueConfig,
        nats_config: NatsConfig,
        durable_name: str = "synthorg_workers",
    ) -> None:
        try:
            import nats  # noqa: F401,PLC0415
        except ImportError as exc:
            msg = (
                "nats-py is required for the distributed task queue. "
                "Install with 'pip install synthorg[distributed]'."
            )
            raise ImportError(msg) from exc
        self._queue_config = queue_config
        self._nats_config = nats_config
        self._durable_name = durable_name
        self._client: Any = None
        self._js: Any = None
        self._sub: Any = None
        self._running = False
        # Per docs/reference/lifecycle-sync.md: serialize start/stop +
        # _running check-and-set under a dedicated lifecycle lock so a
        # concurrent stop cannot race a start.  Hot-path
        # publish/consume traffic does NOT take this lock; the NATS
        # client + JetStream APIs serialize their own state.
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see.
        # Timed-out stops mark the queue unrestartable.  Recovery
        # requires constructing a fresh instance (the existing NATS
        # client may still hold the durable consumer's pull
        # subscription).
        self._stop_failed = False
        self._stop_drain_timeout_seconds = _STOP_DRAIN_TIMEOUT_SECONDS

    @property
    def is_running(self) -> bool:
        """Whether the queue client is connected."""
        return self._running

    async def start(self) -> None:
        """Connect to NATS and ensure the work-queue stream exists.

        Raises:
            BusUnrestartableError: If a previous ``stop()`` exceeded
                its drain timeout and marked the instance unrestartable.
                Callers must construct a fresh instance.
            RuntimeError: If ``start()`` is called while the queue is
                already running. Reconnecting would leak the existing
                client/subscription and attach multiple listeners to
                the same durable consumer.
            BusConnectionError: If the NATS connection cannot be
                established.
            BusStreamError: If stream/consumer setup fails after the
                connection is established. The partially-initialized
                client is drained before the exception propagates.
        """
        async with self._lifecycle_lock:
            if self._stop_failed:
                msg = (
                    "JetStreamTaskQueue is unrestartable: prior stop() "
                    "exceeded the drain timeout; construct a fresh instance"
                )
                raise BusUnrestartableError(msg)
            if self._running:
                logger.warning(
                    WORKERS_QUEUE_START_REJECTED,
                    reason="already_running",
                )
                msg = "JetStreamTaskQueue.start() called while already running"
                raise RuntimeError(msg)
            try:
                await self._connect()
                await self._ensure_stream()
                await self._ensure_consumer()
            except BaseException:
                # Stream or consumer creation failed (or we were cancelled).
                # Drop the partially-initialized client so the caller does
                # not leak a live connection; re-raise so the error
                # surfaces at the call site.
                await self._drain_partial()
                raise
            self._running = True

    async def stop(self) -> None:
        """Close the NATS connection. Idempotent.

        Drains the NATS client whenever one is present, even if
        ``_running`` was never flipped to ``True`` (e.g., when
        ``start()`` raised after ``_connect()`` succeeded).

        Raises:
            BusStopTimeoutError: If the NATS drain exceeds
                ``_stop_drain_timeout_seconds``. Marks the queue
                unrestartable; subsequent ``start()`` will raise
                :class:`BusUnrestartableError`.
        """
        async with self._lifecycle_lock:
            self._running = False
            if self._sub is not None:
                try:
                    await self._sub.unsubscribe()
                except MemoryError, RecursionError:
                    raise
                except Exception as exc:
                    logger.warning(
                        WORKERS_TASK_QUEUE_UNSUBSCRIBE_FAILED,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                self._sub = None
            if self._client is not None:
                try:
                    await asyncio.wait_for(
                        self._client.drain(),
                        timeout=self._stop_drain_timeout_seconds,
                    )
                except TimeoutError as exc:
                    # Hard deadline exceeded. Mark unrestartable and
                    # raise the typed error so the operator can act.
                    self._stop_failed = True
                    logger.warning(
                        WORKERS_TASK_QUEUE_DRAIN_FAILED,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                        timeout_seconds=self._stop_drain_timeout_seconds,
                        note="stop drain exceeded deadline; queue is now unrestartable",
                    )
                    msg = (
                        f"JetStreamTaskQueue.stop() drain exceeded "
                        f"{self._stop_drain_timeout_seconds}s"
                    )
                    raise BusStopTimeoutError(msg) from exc
                except MemoryError, RecursionError:
                    raise
                except Exception as exc:
                    logger.warning(
                        WORKERS_TASK_QUEUE_DRAIN_FAILED,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                self._client = None
                self._js = None

    async def _drain_partial(self) -> None:
        """Tear down any half-initialised connection/consumer after a failed start."""
        if self._sub is not None:
            try:
                await self._sub.unsubscribe()
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    WORKERS_TASK_QUEUE_UNSUBSCRIBE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
            self._sub = None
        if self._client is not None:
            try:
                await self._client.drain()
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    WORKERS_TASK_QUEUE_DRAIN_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
            self._client = None
            self._js = None

    async def _connect(self) -> None:
        """Open the NATS connection, translating failures to domain errors."""
        import nats  # noqa: PLC0415
        from nats.errors import NoServersError  # noqa: PLC0415

        try:
            self._client = await nats.connect(
                servers=[self._nats_config.url],
                reconnect_time_wait=self._nats_config.reconnect_time_wait_seconds,
                max_reconnect_attempts=self._nats_config.max_reconnect_attempts,
                connect_timeout=self._nats_config.connect_timeout_seconds,
                user_credentials=self._nats_config.credentials_path,
            )
        except (TimeoutError, NoServersError, OSError) as exc:
            safe_url = redact_url(self._nats_config.url)
            msg = f"Failed to connect to NATS at {safe_url} for task queue: {safe_error_description(exc)}"  # noqa: E501
            logger.warning(
                WORKERS_TASK_QUEUE_CONNECT_FAILED,
                url=safe_url,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise BusConnectionError(
                msg,
                context={"url": safe_url},
            ) from exc
        self._js = self._client.jetstream()

    async def _ensure_stream(self) -> None:
        """Create the work-queue stream if it does not already exist."""
        from nats.errors import Error as NatsError  # noqa: PLC0415
        from nats.js.api import (  # noqa: PLC0415
            RetentionPolicy,
            StorageType,
            StreamConfig,
        )
        from nats.js.errors import NotFoundError  # noqa: PLC0415

        if self._js is None:
            msg = "JetStream context not initialized"
            raise BusStreamError(msg)

        stream_config = StreamConfig(
            name=self._queue_config.stream_name,
            subjects=[
                f"{self._queue_config.ready_subject_prefix}.>",
                f"{self._queue_config.dead_subject_prefix}.>",
            ],
            retention=RetentionPolicy.WORK_QUEUE,
            storage=StorageType.FILE,
        )
        try:
            try:
                await self._js.stream_info(self._queue_config.stream_name)
            except NotFoundError:
                await self._js.add_stream(stream_config)
            else:
                await self._js.update_stream(stream_config)
        except NatsError as exc:
            msg = (
                f"Failed to set up task queue stream "
                f"{self._queue_config.stream_name}: {safe_error_description(exc)}"
            )
            raise BusStreamError(
                msg,
                context={"stream": self._queue_config.stream_name},
            ) from exc

    async def _ensure_consumer(self) -> None:
        """Create the shared durable pull consumer for all workers.

        Passes ``ack_wait`` and ``max_deliver`` from
        :class:`QueueConfig` so redelivery and dead-letter routing
        behave as documented in the Distributed Runtime design page.
        """
        from nats.errors import Error as NatsError  # noqa: PLC0415
        from nats.js.api import ConsumerConfig  # noqa: PLC0415

        if self._js is None:
            msg = "JetStream context not initialized"
            raise BusStreamError(msg)

        subject = f"{self._queue_config.ready_subject_prefix}.>"
        consumer_config = ConsumerConfig(
            durable_name=self._durable_name,
            ack_wait=float(self._queue_config.ack_wait_seconds),
            max_deliver=self._queue_config.max_deliver,
            filter_subject=subject,
        )
        try:
            self._sub = await self._js.pull_subscribe(
                subject=subject,
                durable=self._durable_name,
                stream=self._queue_config.stream_name,
                config=consumer_config,
            )
        except NatsError as exc:
            msg = f"Failed to create task queue consumer {self._durable_name}: {safe_error_description(exc)}"  # noqa: E501
            raise BusStreamError(
                msg,
                context={
                    "stream": self._queue_config.stream_name,
                    "consumer": self._durable_name,
                },
            ) from exc

    async def publish_claim(self, claim: TaskClaim) -> None:
        """Enqueue a claim for workers to pull.

        Args:
            claim: The task claim to enqueue.
        """
        if self._js is None:
            logger.warning(
                WORKERS_QUEUE_NOT_RUNNING,
                operation="publish_claim",
                task_id=claim.task_id,
            )
            msg = "Task queue is not running"
            raise BusStreamError(msg)
        subject = f"{self._queue_config.ready_subject_prefix}.{claim.task_id}"
        payload = claim.model_dump_json().encode("utf-8")
        await self._js.publish(subject, payload)

    async def next_claim(
        self,
        timeout: float,  # noqa: ASYNC109
    ) -> tuple[TaskClaim, Any] | None:
        """Fetch the next claim from the work queue.

        Returns a ``(claim, raw_message)`` tuple or ``None`` on
        timeout. The raw message must be passed to :meth:`ack` or
        :meth:`nack` when the worker finishes.
        """
        from nats.errors import TimeoutError as NatsTimeoutError  # noqa: PLC0415

        if self._sub is None:
            logger.warning(
                WORKERS_QUEUE_NOT_RUNNING,
                operation="next_claim",
            )
            msg = "Task queue is not running"
            raise BusStreamError(msg)
        try:
            msgs = await self._sub.fetch(batch=1, timeout=timeout)
        except NatsTimeoutError:
            return None
        if not msgs:
            return None
        raw = msgs[0]
        if len(raw.data) > _MAX_CLAIM_PAYLOAD_BYTES:
            logger.warning(
                WORKERS_TASK_QUEUE_CLAIM_PARSE_FAILED,
                reason="payload_too_large",
                size=len(raw.data),
                limit=_MAX_CLAIM_PAYLOAD_BYTES,
            )
            try:
                await raw.ack()
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    WORKERS_TASK_QUEUE_ACK_MALFORMED_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
            return None
        try:
            claim = TaskClaim.model_validate_json(raw.data.decode("utf-8"))
        except ValueError:
            # Malformed claim: terminally ack so it is not redelivered.
            logger.warning(
                WORKERS_TASK_QUEUE_CLAIM_PARSE_FAILED,
                reason="validation_failed",
                size=len(raw.data),
            )
            try:
                await raw.ack()
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    WORKERS_TASK_QUEUE_ACK_MALFORMED_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
            return None
        return claim, raw

    @staticmethod
    async def ack(raw: Any) -> None:
        """Acknowledge successful processing of a claim."""
        await raw.ack()

    @staticmethod
    async def nack(raw: Any, delay_seconds: float = 0.0) -> None:
        """Negative-ack a claim, triggering redelivery after the delay."""
        if delay_seconds > 0:
            await raw.nak(delay=delay_seconds)
        else:
            await raw.nak()
