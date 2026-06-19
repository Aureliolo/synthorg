"""NATS connection lifecycle and JetStream infrastructure setup.

Handles: connect, drain, stream creation, KV bucket creation, and
graceful stop (cancel in-flight fetches, unsubscribe consumers, drain
the client).
"""

import asyncio

from synthorg.communication.bus._nats_state import _NatsState
from synthorg.communication.bus._nats_utils import (
    SUBJECT_CHANNEL_TOKEN,
    SUBJECT_DIRECT_TOKEN,
    redact_url,
)
from synthorg.communication.bus.errors import (
    BusConnectionError,
    BusStopTimeoutError,
    BusStreamError,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.background_tasks import log_task_exceptions
from synthorg.observability.events.communication import (
    COMM_BUS_CONNECTED,
    COMM_BUS_DISCONNECTED,
    COMM_BUS_KV_READ_FAILED,
    COMM_BUS_RECONNECTING,
    COMM_BUS_STOPPED,
    COMM_BUS_STREAM_SCAN_FAILED,
)

logger = get_logger(__name__)


async def connect(state: _NatsState) -> None:
    """Establish the NATS connection, setting ``state.client`` and ``state.js``.

    Raises:
        BusConnectionError: If the connection fails (timeout, no servers,
            or OS-level network error).
    """
    import nats  # noqa: PLC0415
    from nats.errors import NoServersError  # noqa: PLC0415

    async def on_disconnected() -> None:
        """Log a warning when the NATS connection drops."""
        logger.warning(COMM_BUS_DISCONNECTED)

    async def on_reconnected() -> None:
        """Log when the NATS connection is re-established."""
        logger.info(COMM_BUS_CONNECTED, reconnect=True)

    async def on_error(exc: Exception) -> None:
        """Log NATS client errors during reconnection."""
        logger.warning(
            COMM_BUS_RECONNECTING,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )

    try:
        state.client = await nats.connect(
            servers=[state.nats_config.url],
            reconnect_time_wait=state.nats_config.reconnect_time_wait_seconds,
            max_reconnect_attempts=state.nats_config.max_reconnect_attempts,
            connect_timeout=state.nats_config.connect_timeout_seconds,
            user_credentials=state.nats_config.credentials_path,
            disconnected_cb=on_disconnected,
            reconnected_cb=on_reconnected,
            error_cb=on_error,
        )
    except (TimeoutError, NoServersError, OSError) as exc:
        redacted = redact_url(state.nats_config.url)
        msg = f"Failed to connect to NATS at {redacted}: {safe_error_description(exc)}"
        logger.warning(
            COMM_BUS_DISCONNECTED,
            url=redacted,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise BusConnectionError(
            msg,
            context={"url": redacted},
        ) from exc

    state.js = state.client.jetstream()
    logger.info(COMM_BUS_CONNECTED, url=redact_url(state.nats_config.url))


async def drain_partial_client(state: _NatsState) -> None:
    """Drain a connected NATS client after a failed ``start()``.

    Silently swallows drain errors because a drain failure cannot be
    surfaced to the caller -- the original setup exception takes
    precedence.
    """
    client = state.client
    if client is None:
        return
    try:
        await client.drain()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            COMM_BUS_DISCONNECTED,
            phase="drain_partial",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    finally:
        state.client = None
        state.js = None
        state.kv = None


async def ensure_stream(state: _NatsState) -> None:
    """Create the bus stream if it does not already exist.

    Raises:
        BusStreamError: If the JetStream context is uninitialised or
            stream creation fails.
    """
    from nats.errors import Error as NatsError  # noqa: PLC0415
    from nats.js.api import (  # noqa: PLC0415
        RetentionPolicy,
        StorageType,
        StreamConfig,
    )
    from nats.js.errors import NotFoundError  # noqa: PLC0415

    if state.js is None:
        msg = "JetStream context not initialized"
        raise BusStreamError(msg)

    pfx = state.nats_config.stream_name_prefix.lower()
    stream_config = StreamConfig(
        name=state.stream_name,
        subjects=[
            f"{pfx}.bus.{SUBJECT_CHANNEL_TOKEN}.>",
            f"{pfx}.bus.{SUBJECT_DIRECT_TOKEN}.>",
        ],
        retention=RetentionPolicy.LIMITS,
        max_msgs_per_subject=(state.config.retention.max_messages_per_channel),
        storage=StorageType.FILE,
        allow_msg_ttl=True,
        allow_atomic=True,
    )
    try:
        try:
            await state.js.stream_info(state.stream_name)
        except NotFoundError:
            await state.js.add_stream(stream_config)
        else:
            await state.js.update_stream(stream_config)
    except NatsError as exc:
        msg = f"Failed to set up stream {state.stream_name}: {safe_error_description(exc)}"  # noqa: E501
        logger.warning(
            COMM_BUS_STREAM_SCAN_FAILED,
            stream=state.stream_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            phase="ensure_stream",
        )
        raise BusStreamError(
            msg,
            context={"stream": state.stream_name},
        ) from exc


async def ensure_kv_bucket(state: _NatsState) -> None:
    """Create the KV bucket for dynamic channel registration.

    Raises:
        BusStreamError: If the JetStream context is uninitialised or
            bucket creation fails.
    """
    from nats.errors import Error as NatsError  # noqa: PLC0415
    from nats.js.errors import BucketNotFoundError  # noqa: PLC0415

    if state.js is None:
        msg = "JetStream context not initialized"
        raise BusStreamError(msg)

    try:
        try:
            state.kv = await state.js.key_value(state.kv_bucket_name)
        except BucketNotFoundError:
            state.kv = await state.js.create_key_value(
                bucket=state.kv_bucket_name,
            )
    except NatsError as exc:
        msg = f"Failed to set up KV bucket {state.kv_bucket_name}: {safe_error_description(exc)}"  # noqa: E501
        logger.warning(
            COMM_BUS_KV_READ_FAILED,
            channel="*",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            phase="ensure_kv_bucket",
        )
        raise BusStreamError(
            msg,
            context={"bucket": state.kv_bucket_name},
        ) from exc


async def stop(state: _NatsState) -> None:
    """Stop the bus gracefully. Idempotent.

    Per ``docs/reference/lifecycle-sync.md``, holds ``state.lock`` across
    the full body so a concurrent ``start()`` cannot race the teardown.
    Cancels outstanding ``receive()`` calls, unsubscribes consumers, and
    drains the NATS client under a hard deadline. A drain that exceeds
    ``state.stop_drain_timeout_seconds`` marks the state unrestartable
    and raises :class:`BusStopTimeoutError`; the operator must construct
    a fresh state to recover.

    Raises:
        BusStopTimeoutError: If the client drain exceeds
            ``state.stop_drain_timeout_seconds``.
    """
    async with state.lock:
        if not state.running:
            return
        state.running = False
        state.shutdown_event.set()

        for task in list(state.in_flight_fetches):
            task.cancel()
        if state.in_flight_fetches:
            await asyncio.gather(
                *state.in_flight_fetches,
                return_exceptions=True,
            )
        state.in_flight_fetches.clear()

        for key, sub in list(state.subscriptions.items()):
            try:
                await sub.unsubscribe()
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    COMM_BUS_DISCONNECTED,
                    phase="stop_unsubscribe",
                    subscription=str(key),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        state.subscriptions.clear()

        if state.client is not None:
            client = state.client

            async def _drain_client() -> None:
                """Drain the NATS client, logging (not raising) on failure.

                Errors are swallowed here so a timed-out ``stop`` that
                abandons the shielded drain does not surface a late
                "task exception never retrieved" once the orphaned drain
                eventually fails.

                Raises:
                    asyncio.CancelledError: Propagated when the drain task
                        itself is cancelled (not on the ``wait_for``
                        deadline, which leaves the shielded task running).
                """
                try:
                    await client.drain()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    logger.warning(
                        COMM_BUS_DISCONNECTED,
                        phase="stop_drain",
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )

            # Shield the drain so the ``wait_for`` deadline does not
            # cancel the underlying ``client.drain()`` mid-flush; a
            # timed-out stop abandons the drain rather than tearing it
            # down (canonical pattern, see docs/reference/lifecycle-sync.md).
            drain_task: asyncio.Task[None] = asyncio.create_task(_drain_client())
            try:
                await asyncio.wait_for(
                    asyncio.shield(drain_task),
                    timeout=state.stop_drain_timeout_seconds,
                )
            except TimeoutError as exc:
                state.stop_failed = True
                # ERROR (not WARNING): the bus is now permanently
                # unrestartable for the process lifetime, matching every
                # sibling lifecycle service's drain-timeout log level.
                logger.error(
                    COMM_BUS_DISCONNECTED,
                    phase="stop_drain",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    timeout_seconds=state.stop_drain_timeout_seconds,
                    note="drain exceeded deadline; bus is now unrestartable",
                )
                msg = (
                    f"JetStreamMessageBus.stop() drain exceeded "
                    f"{state.stop_drain_timeout_seconds}s"
                )
                # The shielded drain keeps running orphaned past the
                # deadline; log its eventual outcome rather than letting a
                # later failure surface as "task exception never retrieved".
                drain_task.add_done_callback(
                    log_task_exceptions(
                        logger,
                        COMM_BUS_DISCONNECTED,
                        note="orphaned_drain_after_timeout",
                    )
                )
                # Release the retained handles before propagating so a
                # timed-out drain does not leak the dead client.
                state.client = None
                state.js = None
                state.kv = None
                raise BusStopTimeoutError(msg) from exc
            state.client = None
            state.js = None
            state.kv = None

        logger.info(COMM_BUS_STOPPED, backend="nats")
