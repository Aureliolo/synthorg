"""Message reception: fetch loop, ack, envelope building.

The core complexity of the bus -- racing fetch against shutdown,
handling timeouts, building delivery envelopes, and acking messages.
"""

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any

from synthorg.communication.bus._nats_channels import resolve_channel_or_raise
from synthorg.communication.bus._nats_consumers import create_pull_consumer
from synthorg.communication.bus._nats_publish import deserialize_message
from synthorg.communication.bus._nats_state import _NatsState
from synthorg.communication.bus._nats_utils import (
    MAX_BUS_PAYLOAD_BYTES,
    RECEIVE_POLL_WINDOW_SECONDS,
    cancel_if_pending,
    raise_not_subscribed,
    require_running,
)
from synthorg.communication.enums import ChannelType
from synthorg.communication.errors import CommunicationError
from synthorg.communication.subscription import DeliveryEnvelope
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.communication import (
    COMM_BUS_MESSAGE_DESERIALIZE_FAILED,
    COMM_BUS_MESSAGE_TOO_LARGE,
    COMM_BUS_RECEIVE_ERROR,
    COMM_MESSAGE_DELIVERED,
    COMM_RECEIVE_SHUTDOWN,
    COMM_SUBSCRIBER_QUEUE_OVERFLOW,
)

_OVERFLOW_LOG_INTERVAL_SECONDS: float = 60.0
"""Minimum seconds between per-subscriber overflow emissions.

JetStream pauses delivery to a consumer once its unacked count hits
``max_ack_pending``. Without this rate-limit an observer polling a
paused consumer would flood logs every poll; once per minute per
subscriber matches operator dashboard refresh cadence.
"""

_CONSUMER_INFO_PROBE_TIMEOUT_SECONDS: float = 2.0
"""Hard deadline for the best-effort ``consumer_info()`` overflow probe.

The probe is an observability side-effect on the receive hot path, so
it MUST NOT stall receive() or shutdown. If the JetStream control-plane
RPC hangs, we time out quickly and treat the timeout exactly like a
generic probe failure: the rate-limit slot is released and the probe
is retried on the next empty fetch.
"""

logger = get_logger(__name__)


async def resolve_consumer(
    state: _NatsState,
    channel_name: str,
    subscriber_id: str,
) -> Any:
    """Validate preconditions and return the durable pull consumer.

    Creates the consumer lazily for BROADCAST subscribers.

    Returns:
        The durable pull consumer for the (channel, subscriber).
    """
    async with state.lock:
        require_running(state)
    await resolve_channel_or_raise(state, channel_name)
    async with state.lock:
        require_running(state)
        channel = state.channels[channel_name]
        if (
            channel.type != ChannelType.BROADCAST
            and subscriber_id not in channel.subscribers
        ):
            raise_not_subscribed(channel_name, subscriber_id)
        key = (channel_name, subscriber_id)
        sub = state.subscriptions.get(key)
        if sub is None:
            await create_pull_consumer(
                state,
                channel_name,
                subscriber_id,
                channel,
            )
            sub = state.subscriptions[key]
    return sub


async def _maybe_log_overflow(
    state: _NatsState,
    sub: Any,
    *,
    channel_name: str,
    subscriber_id: str,
    max_wait_seconds: float | None = None,
) -> None:
    """Emit ``COMM_SUBSCRIBER_QUEUE_OVERFLOW`` if the consumer is paused.

    Called from the receive path when a fetch returns empty. Queries
    ``consumer_info()`` to check whether ``num_ack_pending`` has hit
    the configured ``max_ack_pending`` cap -- the observable signal
    that JetStream has paused delivery to this consumer. Rate-limited
    per ``(channel, subscriber)`` at
    :data:`_OVERFLOW_LOG_INTERVAL_SECONDS`.

    ``max_wait_seconds`` clamps the ``consumer_info()`` probe to the
    caller's remaining receive budget so a bounded ``receive(timeout=
    0.1)`` cannot be extended to ``0.1 + 2s`` by the probe. Pass the
    remaining budget (e.g. ``deadline - time.monotonic()``) from
    bounded callers; blocking callers may omit it to use the default
    :data:`_CONSUMER_INFO_PROBE_TIMEOUT_SECONDS`. When the clamped
    timeout is non-positive the probe is skipped entirely (rate-limit
    slot released so the next empty fetch retries).

    Best-effort: ``consumer_info()`` failures are swallowed so an
    observability probe never breaks the receive loop.
    """
    cap = state.config.retention.max_subscriber_queue_size
    key = (channel_name, subscriber_id)
    now = state.clock.monotonic()
    last = state.last_overflow_log.get(key, 0.0)
    if now - last < _OVERFLOW_LOG_INTERVAL_SECONDS:
        return
    # Claim a provisional rate-limit slot *before* awaiting
    # ``consumer_info()`` so concurrent callers on the same
    # ``(channel, subscriber)`` key cannot all pass the window check
    # and pile on duplicate probes. On any path that does NOT emit
    # the overflow event (probe failure, healthy consumer, budget
    # exhausted) we release the slot via ``pop`` so a transient empty
    # fetch on a healthy consumer does not suppress the next *real*
    # overflow warning for up to ``_OVERFLOW_LOG_INTERVAL_SECONDS``.
    state.last_overflow_log[key] = now
    probe_timeout = _CONSUMER_INFO_PROBE_TIMEOUT_SECONDS
    if max_wait_seconds is not None:
        probe_timeout = min(probe_timeout, max(0.0, max_wait_seconds))
    if probe_timeout <= 0.0:
        # Caller's remaining budget is already exhausted -- skip the
        # probe so receive() can return promptly. The next empty fetch
        # will retry with a fresh budget.
        state.last_overflow_log.pop(key, None)
        return
    # Race the probe against shutdown so ``receive_blocking()`` (which
    # has no caller deadline) cannot hold shutdown open for the full
    # probe_timeout budget. If ``state.shutdown_event`` fires first we
    # short-circuit through the same non-emission path as a probe
    # failure; otherwise we use the consumer_info() result.
    probe_task: asyncio.Task[Any] = asyncio.create_task(sub.consumer_info())
    shutdown_task: asyncio.Task[Any] = asyncio.create_task(
        state.shutdown_event.wait(),
    )
    try:
        done, _ = await asyncio.wait(
            {probe_task, shutdown_task},
            timeout=probe_timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
    except BaseException:
        # Non-emission path -- release the provisional slot before
        # re-raising so a CancelledError (or other system error) here
        # does not suppress the next real overflow warning for
        # ``_OVERFLOW_LOG_INTERVAL_SECONDS``.
        state.last_overflow_log.pop(key, None)
        probe_task.cancel()
        shutdown_task.cancel()
        raise
    # Clean up the loser (timeout, or the task that didn't finish).
    # The slot must be released on the critical-re-raise path so a
    # MemoryError / RecursionError does not suppress the next real
    # overflow warning for ``_OVERFLOW_LOG_INTERVAL_SECONDS``.
    if probe_task not in done:
        probe_task.cancel()
        try:
            await probe_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            if isinstance(exc, (MemoryError, RecursionError)):
                state.last_overflow_log.pop(key, None)
            reraise_critical(exc)
    if shutdown_task not in done:
        shutdown_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await shutdown_task
    # Shutdown wins (or timed out with no probe result): release the
    # rate-limit slot and exit through the non-emission path. The next
    # empty fetch after a restart will retry the probe cleanly.
    if probe_task not in done:
        state.last_overflow_log.pop(key, None)
        return
    try:
        info = probe_task.result()
    except Exception as exc:
        # Non-emission path: release the slot on both critical re-raise
        # AND probe-RPC-failure paths so the next empty fetch can retry
        # without suppressing a real overflow warning for the next
        # ``_OVERFLOW_LOG_INTERVAL_SECONDS``.
        state.last_overflow_log.pop(key, None)
        reraise_critical(exc)
        return
    num_pending = getattr(info, "num_ack_pending", 0)
    if num_pending < cap:
        state.last_overflow_log.pop(key, None)
        return
    logger.warning(
        COMM_SUBSCRIBER_QUEUE_OVERFLOW,
        channel=channel_name,
        subscriber=subscriber_id,
        queue_size=cap,
        drop_policy="delivery_paused",
        backend="nats",
        num_ack_pending=num_pending,
    )


async def fetch_with_shutdown(
    state: _NatsState,
    sub: Any,
    timeout: float,  # noqa: ASYNC109
    *,
    channel_name: str,
    subscriber_id: str,
) -> list[Any] | None:
    """Fetch at most one message, racing against the shutdown event.

    Returns:
        The fetched messages (at most one), an empty list on clean
        timeout, or ``None`` on shutdown, cancellation, or internal
        error.
    """
    from nats.errors import TimeoutError as NatsTimeoutError  # noqa: PLC0415

    fetch_task: asyncio.Task[Any] = asyncio.create_task(
        sub.fetch(batch=1, timeout=timeout),
    )
    shutdown_task: asyncio.Task[Any] = asyncio.create_task(
        state.shutdown_event.wait(),
    )
    state.in_flight_fetches.add(fetch_task)
    state.in_flight_fetches.add(shutdown_task)

    try:
        done, _ = await asyncio.wait(
            {fetch_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    except BaseException:
        fetch_task.cancel()
        shutdown_task.cancel()
        raise
    finally:
        state.in_flight_fetches.discard(fetch_task)
        state.in_flight_fetches.discard(shutdown_task)

    await cancel_if_pending(fetch_task)
    await cancel_if_pending(shutdown_task)

    if shutdown_task in done and fetch_task not in done:
        logger.debug(
            COMM_RECEIVE_SHUTDOWN,
            channel=channel_name,
            subscriber=subscriber_id,
        )
        return None

    try:
        result: list[Any] = fetch_task.result()
    except NatsTimeoutError:
        return []
    except asyncio.CancelledError:
        return None
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            COMM_BUS_RECEIVE_ERROR,
            channel=channel_name,
            subscriber=subscriber_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    return result


async def try_ack(
    msg: Any,
    *,
    channel_name: str,
    subscriber_id: str,
) -> bool:
    """Attempt to ack a fetched JetStream message.

    Returns:
        ``True`` on successful ack, ``False`` on failure.
    """
    try:
        await msg.ack()
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            COMM_BUS_RECEIVE_ERROR,
            channel=channel_name,
            subscriber=subscriber_id,
            phase="ack",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return False
    return True


async def build_envelope(
    msgs: list[Any] | None,
    *,
    channel_name: str,
    subscriber_id: str,
) -> DeliveryEnvelope | None:
    """Wrap a fetched JetStream message in a deferred-ack envelope.

    The returned envelope's ``ack()`` callable acknowledges the
    JetStream message; callers MUST invoke it after the subscriber's
    local queue has accepted delivery so that an ack-then-deliver-
    failure cannot drop the message. Pre-parse rejection paths
    (oversized payload, deserialise error) still ack immediately
    because there is nothing downstream to deliver.

    Returns:
        A deferred-ack ``DeliveryEnvelope``, or ``None`` when there is
        no message or it was rejected pre-parse.
    """
    if not msgs:
        return None

    msg = msgs[0]
    if len(msg.data) > MAX_BUS_PAYLOAD_BYTES:
        logger.warning(
            COMM_BUS_MESSAGE_TOO_LARGE,
            channel=channel_name,
            subscriber=subscriber_id,
            size=len(msg.data),
            limit=MAX_BUS_PAYLOAD_BYTES,
        )
        await try_ack(
            msg,
            channel_name=channel_name,
            subscriber_id=subscriber_id,
        )
        return None

    try:
        parsed = deserialize_message(msg.data)
    except ValueError as exc:
        logger.warning(
            COMM_BUS_MESSAGE_DESERIALIZE_FAILED,
            channel=channel_name,
            subscriber=subscriber_id,
            size=len(msg.data),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        await try_ack(
            msg,
            channel_name=channel_name,
            subscriber_id=subscriber_id,
        )
        return None

    async def deferred_ack() -> None:
        """Acknowledge the JetStream message after local delivery.

        Raises ``CommunicationError`` when the underlying ``try_ack``
        reports failure. ``try_ack`` already logs the underlying NATS
        exception; surfacing a domain error here makes the failure
        visible to the consumer-side ``await envelope.ack()`` call so
        a silent ack-drop cannot leave JetStream redelivering the same
        message into an oblivious downstream loop.

        Raises:
            CommunicationError: When the underlying ``try_ack`` reports
                failure.
        """
        acked = await try_ack(
            msg,
            channel_name=channel_name,
            subscriber_id=subscriber_id,
        )
        if not acked:
            msg_text = "Deferred JetStream ack failed after local delivery"
            raise CommunicationError(msg_text)

    envelope = DeliveryEnvelope(
        message=parsed,
        channel_name=channel_name,
        delivered_at=datetime.now(UTC),
        ack=deferred_ack,
    )
    logger.debug(
        COMM_MESSAGE_DELIVERED,
        channel=channel_name,
        subscriber=subscriber_id,
        message_id=str(parsed.id),
        backend="nats",
    )
    return envelope


async def receive_blocking(
    state: _NatsState,
    channel_name: str,
    subscriber_id: str,
    sub: Any,
) -> DeliveryEnvelope | None:
    """Block on a fetch loop until a message arrives or the bus stops.

    Returns:
        The next delivery envelope, or ``None`` when the bus stops.
    """
    # lint-allow: long-running-loop-kill-switch -- per-call subscribe pump.
    while True:
        if state.shutdown_event.is_set():
            return None
        msgs = await fetch_with_shutdown(
            state,
            sub,
            RECEIVE_POLL_WINDOW_SECONDS,
            channel_name=channel_name,
            subscriber_id=subscriber_id,
        )
        if msgs is None:
            return None
        if not msgs:
            # Blocking caller has no receive deadline, so let the
            # probe use its default 2s ceiling.
            await _maybe_log_overflow(
                state,
                sub,
                channel_name=channel_name,
                subscriber_id=subscriber_id,
            )
            continue
        envelope = await build_envelope(
            msgs,
            channel_name=channel_name,
            subscriber_id=subscriber_id,
        )
        if envelope is not None:
            return envelope


async def receive_with_timeout(
    state: _NatsState,
    channel_name: str,
    subscriber_id: str,
    sub: Any,
    timeout: float,  # noqa: ASYNC109
) -> DeliveryEnvelope | None:
    """Wait up to ``timeout`` seconds across one or more fetch polls.

    Returns:
        The next delivery envelope, or ``None`` on timeout or shutdown.
    """
    deadline = state.clock.monotonic() + timeout
    # lint-allow: long-running-loop-kill-switch -- per-call timed pump.
    while True:
        remaining = deadline - state.clock.monotonic()
        if remaining <= 0.0:
            return None
        if state.shutdown_event.is_set():
            return None
        poll = min(remaining, RECEIVE_POLL_WINDOW_SECONDS)
        msgs = await fetch_with_shutdown(
            state,
            sub,
            poll,
            channel_name=channel_name,
            subscriber_id=subscriber_id,
        )
        if msgs is None:
            return None
        if not msgs:
            # Clamp the overflow probe to the caller's remaining
            # receive budget so ``receive(timeout=0.1)`` cannot be
            # extended by the full 2s probe ceiling. If the budget
            # is already exhausted the helper skips the probe.
            budget = deadline - state.clock.monotonic()
            await _maybe_log_overflow(
                state,
                sub,
                channel_name=channel_name,
                subscriber_id=subscriber_id,
                max_wait_seconds=budget,
            )
            continue
        envelope = await build_envelope(
            msgs,
            channel_name=channel_name,
            subscriber_id=subscriber_id,
        )
        if envelope is not None:
            return envelope


async def receive(
    state: _NatsState,
    channel_name: str,
    subscriber_id: str,
    *,
    timeout: float | None = None,  # noqa: ASYNC109
) -> DeliveryEnvelope | None:
    """Receive the next message from the durable consumer.

    Returns:
        The next delivery envelope, or ``None`` on timeout/shutdown.
    """
    sub = await resolve_consumer(state, channel_name, subscriber_id)
    if timeout is None:
        return await receive_blocking(state, channel_name, subscriber_id, sub)
    return await receive_with_timeout(state, channel_name, subscriber_id, sub, timeout)
