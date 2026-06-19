# module-kind: adapter
"""JetStream transport bootstrap for the task-claim work queue.

Connection, stream, and consumer setup for
:class:`~synthorg.workers.claim.JetStreamTaskQueue`. Kept as free
functions that take the resolved config and return the live handles, so
the queue's ``start`` wires them together while the ``nats-py``-specific
setup detail lives here. ``nats-py`` is an optional dependency, so each
function imports it lazily and the type-only handles stay guarded.
"""

from typing import TYPE_CHECKING

from synthorg.communication.bus import redact_url
from synthorg.communication.bus.errors import BusConnectionError, BusStreamError
from synthorg.communication.config import NatsConfig
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workers import (
    WORKERS_TASK_QUEUE_CONNECT_FAILED,
    WORKERS_TASK_QUEUE_CONSUMER_SETUP_FAILED,
    WORKERS_TASK_QUEUE_STREAM_SETUP_FAILED,
)
from synthorg.workers.config import QueueConfig

if TYPE_CHECKING:
    from nats.aio.client import Client as NatsClient
    from nats.js import JetStreamContext

    PullSubscription = JetStreamContext.PullSubscription

logger = get_logger(__name__)


async def connect(nats_config: NatsConfig) -> tuple[NatsClient, JetStreamContext]:
    """Open the NATS connection, translating failures to domain errors.

    Args:
        nats_config: Connection settings (URL, reconnect, credentials).

    Returns:
        The connected client and its JetStream context.

    Raises:
        BusConnectionError: When the NATS connection cannot be
            established.
    """
    import nats  # noqa: PLC0415
    from nats.errors import NoServersError  # noqa: PLC0415

    try:
        client = await nats.connect(
            servers=[nats_config.url],
            reconnect_time_wait=nats_config.reconnect_time_wait_seconds,
            max_reconnect_attempts=nats_config.max_reconnect_attempts,
            connect_timeout=nats_config.connect_timeout_seconds,
            user_credentials=nats_config.credentials_path,
        )
    except (TimeoutError, NoServersError, OSError) as exc:
        safe_url = redact_url(nats_config.url)
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
    return client, client.jetstream()


async def ensure_stream(
    js: JetStreamContext,
    queue_config: QueueConfig,
) -> None:
    """Create the work-queue stream if it does not already exist.

    Args:
        js: The live JetStream context.
        queue_config: Stream naming and retention settings.

    Raises:
        BusStreamError: When the stream setup fails.
    """
    from nats.errors import Error as NatsError  # noqa: PLC0415
    from nats.js.api import (  # noqa: PLC0415
        RetentionPolicy,
        StorageType,
        StreamConfig,
    )
    from nats.js.errors import NotFoundError  # noqa: PLC0415

    stream_config = StreamConfig(
        name=queue_config.stream_name,
        subjects=[
            f"{queue_config.ready_subject_prefix}.>",
            f"{queue_config.dead_subject_prefix}.>",
        ],
        retention=RetentionPolicy.WORK_QUEUE,
        storage=StorageType.FILE,
        max_msgs=queue_config.stream_max_msgs,
        max_bytes=queue_config.stream_max_bytes,
    )
    try:
        try:
            await js.stream_info(queue_config.stream_name)
        except NotFoundError:
            await js.add_stream(stream_config)
        else:
            await js.update_stream(stream_config)
    except NatsError as exc:
        msg = (
            f"Failed to set up task queue stream "
            f"{queue_config.stream_name}: {safe_error_description(exc)}"
        )
        logger.warning(
            WORKERS_TASK_QUEUE_STREAM_SETUP_FAILED,
            stream_name=queue_config.stream_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise BusStreamError(
            msg,
            context={"stream": queue_config.stream_name},
        ) from exc


async def ensure_consumer(
    js: JetStreamContext,
    queue_config: QueueConfig,
    durable_name: str,
) -> PullSubscription:
    """Create the shared durable pull consumer for all workers.

    Passes ``ack_wait`` and ``max_deliver`` from :class:`QueueConfig` so
    redelivery and dead-letter routing behave as documented in the
    Distributed Runtime design page.

    Args:
        js: The live JetStream context.
        queue_config: Consumer ack/redelivery settings.
        durable_name: The shared durable consumer name.

    Returns:
        The pull subscription all workers fetch from.

    Raises:
        BusStreamError: When the consumer cannot be created.
    """
    from nats.errors import Error as NatsError  # noqa: PLC0415
    from nats.js.api import ConsumerConfig  # noqa: PLC0415

    subject = f"{queue_config.ready_subject_prefix}.>"
    consumer_config = ConsumerConfig(
        durable_name=durable_name,
        ack_wait=float(queue_config.ack_wait_seconds),
        max_deliver=queue_config.max_deliver,
        max_ack_pending=queue_config.max_ack_pending,
        filter_subject=subject,
    )
    try:
        return await js.pull_subscribe(
            subject=subject,
            durable=durable_name,
            stream=queue_config.stream_name,
            config=consumer_config,
        )
    except NatsError as exc:
        msg = f"Failed to create task queue consumer {durable_name}: {safe_error_description(exc)}"  # noqa: E501
        logger.warning(
            WORKERS_TASK_QUEUE_CONSUMER_SETUP_FAILED,
            consumer=durable_name,
            stream_name=queue_config.stream_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise BusStreamError(
            msg,
            context={
                "stream": queue_config.stream_name,
                "consumer": durable_name,
            },
        ) from exc
