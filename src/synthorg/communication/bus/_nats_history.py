"""History scanning: query JetStream for bounded message history.

Uses ephemeral pull consumers with ``DeliverPolicy.ALL`` and
``AckPolicy.NONE`` to scan a subject's history without affecting
durable consumer state.
"""

from typing import TYPE_CHECKING, Final

from synthorg.communication.bus._nats_channels import (
    resolve_channel_or_raise,
    subject_for_channel,
)
from synthorg.communication.bus._nats_publish import deserialize_message
from synthorg.communication.bus._nats_state import _NatsState
from synthorg.communication.bus.errors import BusStreamError
from synthorg.communication.message import Message
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.communication import (
    COMM_BUS_MESSAGE_DESERIALIZE_FAILED,
    COMM_BUS_STREAM_SCAN_FAILED,
    COMM_HISTORY_QUERIED,
)

if TYPE_CHECKING:
    # nats-py is an optional dependency, so these stay guarded for clean import
    # when it is absent.
    from nats.aio.msg import Msg
    from nats.js import JetStreamContext

    # PullSubscription is a nested class on JetStreamContext, not a
    # module-level export, so it cannot be imported directly.
    PullSubscription = JetStreamContext.PullSubscription

logger = get_logger(__name__)

_DEFAULT_BATCH_SIZE: Final[int] = 100
_DEFAULT_FETCH_TIMEOUT_SECONDS: Final[float] = 0.5


async def create_history_scan_consumer(
    js: JetStreamContext,
    subject: str,
    stream_name: str,
) -> PullSubscription | None:
    """Create the ephemeral pull consumer used by history scans.

    Returns:
        The pull-subscription, or ``None`` if the stream/subject is not
        found.

    Raises:
        BusStreamError: If consumer creation fails for a reason other
            than not-found.
    """
    from nats.js.api import (  # noqa: PLC0415
        AckPolicy,
        ConsumerConfig,
        DeliverPolicy,
    )
    from nats.js.errors import NotFoundError  # noqa: PLC0415

    consumer_config = ConsumerConfig(
        deliver_policy=DeliverPolicy.ALL,
        ack_policy=AckPolicy.NONE,
        filter_subject=subject,
    )
    try:
        return await js.pull_subscribe(
            subject=subject,
            stream=stream_name,
            config=consumer_config,
        )
    except NotFoundError:
        return None
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            COMM_BUS_STREAM_SCAN_FAILED,
            stream=stream_name,
            subject=subject,
            phase="subscribe",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"History scan consumer creation failed for {subject}: {safe_error_description(exc)}"  # noqa: E501
        raise BusStreamError(
            msg, context={"stream": stream_name, "subject": subject}
        ) from exc


async def collect_history_batches(
    psub: PullSubscription,
    subject: str,
    stream_name: str,
    *,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    fetch_timeout_seconds: float = _DEFAULT_FETCH_TIMEOUT_SECONDS,
) -> list[Message]:
    """Drain the history consumer into a list, stopping on idle timeout.

    Args:
        psub: Pull-subscription returned by the history consumer bootstrap.
        subject: Subject being scanned.
        stream_name: Stream being scanned.
        batch_size: Messages fetched per JetStream pull.
        fetch_timeout_seconds: Per-batch fetch timeout.

    Returns:
        The parsed messages drained from the consumer (empty on idle
        timeout with no data).

    Raises:
        BusStreamError: If a batch fetch fails (non-timeout error).
    """
    parsed_messages: list[Message] = []
    # lint-allow: long-running-loop-kill-switch -- one-shot history replay.
    while True:
        try:
            batch = await psub.fetch(batch=batch_size, timeout=fetch_timeout_seconds)
        except TimeoutError:
            # The pull fetch raises a timeout once the consumer is drained.
            # nats.py may surface either its own TimeoutError (a subclass of
            # the builtin) or a bare asyncio/builtin TimeoutError; both mean
            # idle, so catch the builtin parent to cover every variant.
            return parsed_messages
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                COMM_BUS_STREAM_SCAN_FAILED,
                stream=stream_name,
                subject=subject,
                phase="fetch",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"History batch fetch failed for {subject}: {safe_error_description(exc)}"  # noqa: E501
            raise BusStreamError(
                msg, context={"stream": stream_name, "subject": subject}
            ) from exc
        if not batch:
            return parsed_messages
        for raw in batch:
            parsed = try_parse_matching(raw, subject)
            if parsed is not None:
                parsed_messages.append(parsed)


async def unsubscribe_history_consumer(
    psub: PullSubscription,
    subject: str,
    stream_name: str,
) -> None:
    """Best-effort teardown for an ephemeral history consumer."""
    try:
        await psub.unsubscribe()
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            COMM_BUS_STREAM_SCAN_FAILED,
            stream=stream_name,
            subject=subject,
            phase="unsubscribe",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


def try_parse_matching(raw: Msg, subject: str) -> Message | None:
    """Parse the raw message if it matches the target subject.

    Returns:
        The deserialised ``Message``, or ``None`` when the subject does
        not match or the payload fails to deserialise.
    """
    if raw.subject != subject or raw.data is None:
        return None
    try:
        return deserialize_message(raw.data)
    except ValueError:
        logger.warning(
            COMM_BUS_MESSAGE_DESERIALIZE_FAILED,
            subject=subject,
            size=len(raw.data),
            phase="history_scan",
        )
        return None


async def scan_stream_for_subject(  # noqa: PLR0913
    state: _NatsState,
    js: JetStreamContext | None,
    *,
    subject: str,
    max_to_return: int,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    fetch_timeout_seconds: float = _DEFAULT_FETCH_TIMEOUT_SECONDS,
) -> list[Message]:
    """Collect the most recent messages on a subject, oldest-first.

    Args:
        state: NATS state.
        js: JetStream handle.
        subject: Subject being scanned.
        max_to_return: Cap on returned messages.
        batch_size: Messages fetched per JetStream pull. Mirrors the
            ``communication.nats_history_batch_size`` setting; callers
            resolve the current value via
            ``ConfigResolver.get_communication_bridge_config()`` and
            pass it in.
        fetch_timeout_seconds: Per-batch fetch timeout. Mirrors the
            ``communication.nats_history_fetch_timeout_seconds`` setting.

    Returns:
        The most recent messages on the subject (oldest-first), capped
        at ``max_to_return``.
    """
    if js is None:
        return []

    psub = await create_history_scan_consumer(js, subject, state.stream_name)
    if psub is None:
        return []

    try:
        parsed_messages = await collect_history_batches(
            psub,
            subject,
            state.stream_name,
            batch_size=batch_size,
            fetch_timeout_seconds=fetch_timeout_seconds,
        )
    finally:
        await unsubscribe_history_consumer(psub, subject, state.stream_name)

    if len(parsed_messages) <= max_to_return:
        return parsed_messages
    return parsed_messages[-max_to_return:]


async def get_channel_history(
    state: _NatsState,
    channel_name: str,
    *,
    limit: int | None = None,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    fetch_timeout_seconds: float = _DEFAULT_FETCH_TIMEOUT_SECONDS,
) -> tuple[Message, ...]:
    """Get message history for a channel.

    Args:
        state: NATS state.
        channel_name: Channel to query.
        limit: Optional override on the retention default.
        batch_size: Forwarded to :func:`scan_stream_for_subject`;
            mirrors ``communication.nats_history_batch_size``.
        fetch_timeout_seconds: Forwarded to
            :func:`scan_stream_for_subject`; mirrors
            ``communication.nats_history_fetch_timeout_seconds``.

    Returns:
        The channel's message history (oldest-first), bounded by
        ``limit`` or the retention default.
    """
    channel = await resolve_channel_or_raise(state, channel_name)
    async with state.lock:
        prefix = state.nats_config.stream_name_prefix
        subject = subject_for_channel(prefix, channel)
        js = state.js

    if limit is not None and limit <= 0:
        logger.debug(
            COMM_HISTORY_QUERIED,
            channel=channel_name,
            count=0,
            limit=limit,
            backend="nats",
        )
        return ()

    max_to_return = (
        limit if limit is not None else state.config.retention.max_messages_per_channel
    )

    messages = await scan_stream_for_subject(
        state,
        js,
        subject=subject,
        max_to_return=max_to_return,
        batch_size=batch_size,
        fetch_timeout_seconds=fetch_timeout_seconds,
    )

    logger.debug(
        COMM_HISTORY_QUERIED,
        channel=channel_name,
        count=len(messages),
        limit=limit,
        backend="nats",
    )
    return tuple(messages)
