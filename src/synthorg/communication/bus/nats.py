"""NATS JetStream message bus backend.

Thin facade over focused submodules. Delegates all logic to:

- ``_nats_connection``: connect, drain, stream/KV setup, stop
- ``_nats_channels``: channel creation, resolution, listing, subjects
- ``_nats_consumers``: subscribe, unsubscribe, pull consumer lifecycle
- ``_nats_kv``: KV bucket read/write/scan
- ``_nats_publish``: publish, send_direct, serialization
- ``_nats_receive``: receive loop, fetch, ack, envelope building
- ``_nats_history``: history scanning with ephemeral consumers
- ``_nats_state``: shared mutable state dataclass
- ``_nats_utils``: pure utilities and constants

See ``docs/design/distributed-runtime.md`` for the full design.

Note: ``nats-py`` is an optional dependency (``pip install
synthorg[distributed]``). Importing this module raises
``ImportError`` if the package is not installed.
"""

from collections.abc import Sequence

from synthorg.communication.bus import _nats_channels as _ch
from synthorg.communication.bus import _nats_connection as _conn
from synthorg.communication.bus import _nats_consumers as _cons
from synthorg.communication.bus import _nats_history as _hist
from synthorg.communication.bus import _nats_publish as _pub
from synthorg.communication.bus import _nats_receive as _recv
from synthorg.communication.bus._nats_state import create_state
from synthorg.communication.bus._nats_utils import require_running
from synthorg.communication.bus.errors import BusUnrestartableError
from synthorg.communication.channel import Channel
from synthorg.communication.config import MessageBusConfig
from synthorg.communication.enums import ChannelType
from synthorg.communication.errors import MessageBusAlreadyRunningError
from synthorg.communication.message import Message
from synthorg.communication.subscription import (
    DeliveryEnvelope,
    Subscription,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.communication import (
    COMM_BUS_ALREADY_RUNNING,
    COMM_BUS_HEALTH_CHECK_FAILED,
    COMM_BUS_START_REJECTED,
    COMM_BUS_STARTED,
    COMM_BUS_STREAM_SCAN_FAILED,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)


class JetStreamMessageBus:
    """NATS JetStream-backed message bus.

    Implements the :class:`MessageBus` protocol using durable pull
    consumers as the per-(channel, subscriber) queue primitive.

    Args:
        config: Bus configuration (channels, retention). The
            ``nats`` sub-block must be non-``None``.

    Raises:
        ValueError: If ``config.nats`` is ``None``.
        ImportError: If ``nats-py`` is not installed.
    """

    def __init__(self, *, config: MessageBusConfig) -> None:
        if config.nats is None:
            msg = (
                "JetStreamMessageBus requires config.nats to be set. "
                "Provide a NatsConfig in MessageBusConfig.nats."
            )
            raise ValueError(msg)
        try:
            import nats  # noqa: F401,PLC0415
        except ImportError as exc:
            msg = (
                "nats-py is required for the JetStream bus backend. "
                "Install with 'pip install synthorg[distributed]'."
            )
            raise ImportError(msg) from exc
        self._state = create_state(config)
        self._config_resolver: ConfigResolver | None = None

    def set_config_resolver(self, resolver: ConfigResolver) -> None:
        """Inject the ConfigResolver after construction.

        History queries (:meth:`get_channel_history`) read the
        operator-tuned ``communication.nats_history_batch_size`` and
        ``communication.nats_history_fetch_timeout_seconds`` settings
        on each call when a resolver is wired. Called from the API
        startup hook after ``AppState`` is built.
        """
        self._config_resolver = resolver

    @property
    def is_running(self) -> bool:
        """Whether the bus client thinks it is running.

        Local state check: flipped by :meth:`start` / :meth:`stop`.
        Does not reflect live server reachability -- use
        :meth:`health_check` for that.
        """
        return self._state.running

    async def health_check(self) -> bool:
        """Live probe against the NATS server.

        Checks that the client is both locally marked running and
        actually connected, then calls ``nc.flush(timeout=<budget>)``,
        which waits for any pending publishes to ack and, when the
        queue is empty, resolves to a broker round-trip. The flush
        timeout is read from
        :class:`NatsConfig.health_flush_timeout_seconds` and is
        deliberately tight (default 2s) so a stuck probe fails fast
        rather than blocking the health endpoint.  Returns ``False``
        instead of raising when the probe fails so the caller can
        treat the bus as degraded without its own ``try``/``except``.
        Flush exceptions are logged at WARNING with typed error info
        and a scrubbed description so operators can distinguish
        broker-unreachable from client-logic bugs without leaking
        credentials from the connection URL: the NATS url can
        include user:pass and serialized traceback frame-locals are
        a known leak vector.

        Returns:
            ``True`` when the bus is running, connected, and the flush
            round-trip succeeds; ``False`` otherwise.
        """
        state = self._state
        if not state.running:
            return False
        client = state.client
        if client is None or not client.is_connected:
            return False
        try:
            # nats-py's flush() is annotated `timeout: int`, but the
            # runtime call forwards to ``asyncio.wait_for`` which accepts
            # floats.  Pass the configured float directly to preserve
            # sub-second precision (useful for tight probe budgets and
            # deterministic tests); type: ignore is for the upstream
            # annotation only.
            await client.flush(
                timeout=state.nats_config.health_flush_timeout_seconds,  # type: ignore[arg-type]
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                COMM_BUS_HEALTH_CHECK_FAILED,
                phase="flush",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        return True

    async def start(self) -> None:
        """Connect to NATS, create the stream, and register channels.

        Raises:
            BusUnrestartableError: If a prior ``stop()`` exceeded its
                drain timeout and marked the bus unrestartable.
            MessageBusAlreadyRunningError: If already running.
            BusConnectionError: If connection to NATS fails.
            BusStreamError: If stream or KV bucket setup fails.
        """
        state = self._state
        async with state.lock:
            if state.stop_failed:
                msg = (
                    "JetStreamMessageBus is unrestartable: prior stop() "
                    "exceeded the drain timeout; construct a fresh instance"
                )
                logger.warning(
                    COMM_BUS_START_REJECTED,
                    reason="unrestartable",
                    error_type=BusUnrestartableError.__name__,
                )
                raise BusUnrestartableError(msg)
            if state.running:
                msg = "Message bus is already running"
                logger.warning(COMM_BUS_ALREADY_RUNNING)
                raise MessageBusAlreadyRunningError(msg)

            try:
                await _conn.connect(state)
                await _conn.ensure_stream(state)
                await _conn.ensure_kv_bucket(state)
            except BaseException:
                await _conn.drain_partial_client(state)
                raise

            for name in state.config.channels:
                ch = Channel(name=name, type=ChannelType.TOPIC)
                state.channels[name] = ch

            state.running = True
            state.shutdown_event.clear()

            # Emit the success event INSIDE the lock so the structured
            # log accurately reflects the held-lock state. Per
            # ``docs/reference/lifecycle-sync.md``, lifecycle events
            # MUST land while the lock is held -- a log emitted after
            # release could be reordered with a racing stop() and
            # mislead operators about the true lifecycle order.
            logger.info(
                COMM_BUS_STARTED,
                channels_created=len(state.config.channels),
                backend="nats",
            )

    async def stop(self) -> None:
        """Stop the bus gracefully. Idempotent."""
        await _conn.stop(self._state)

    async def publish(
        self,
        message: Message,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        """Publish a message to its channel via JetStream."""
        await _pub.publish(self._state, message, ttl_seconds=ttl_seconds)

    async def send_direct(
        self,
        message: Message,
        *,
        recipient: str,
        ttl_seconds: float | None = None,
    ) -> None:
        """Send a direct message, creating the DIRECT channel lazily."""
        await _pub.send_direct(
            self._state,
            message,
            recipient=recipient,
            ttl_seconds=ttl_seconds,
        )

    async def publish_batch(
        self,
        messages: Sequence[Message],
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        """Publish multiple messages using pipelined async publishes."""
        await _pub.publish_batch(
            self._state,
            messages,
            ttl_seconds=ttl_seconds,
        )

    async def subscribe(
        self,
        channel_name: str,
        subscriber_id: str,
    ) -> Subscription:
        """Subscribe an agent to a channel via a durable pull consumer.

        Returns:
            The ``Subscription`` handle for the new consumer.
        """
        return await _cons.subscribe(self._state, channel_name, subscriber_id)

    async def unsubscribe(
        self,
        channel_name: str,
        subscriber_id: str,
    ) -> None:
        """Remove a subscription and tear down the pull consumer."""
        await _cons.unsubscribe(self._state, channel_name, subscriber_id)

    async def receive(
        self,
        channel_name: str,
        subscriber_id: str,
        *,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> DeliveryEnvelope | None:
        """Receive the next message from the durable consumer.

        Returns:
            The next delivery envelope, or ``None`` on timeout/shutdown.
        """
        return await _recv.receive(
            self._state,
            channel_name,
            subscriber_id,
            timeout=timeout,
        )

    async def create_channel(self, channel: Channel) -> Channel:
        """Create a new channel.

        Returns:
            The created channel.
        """
        async with self._state.lock:
            require_running(self._state)
        return await _ch.create_channel(self._state, channel)

    async def get_channel(self, channel_name: str) -> Channel:
        """Get a channel by name.

        Returns:
            The resolved channel.
        """
        return await _ch.resolve_channel_or_raise(self._state, channel_name)

    async def list_channels(self) -> tuple[Channel, ...]:
        """List all channels, including those from peer processes.

        Returns:
            All channels known locally or discovered in the KV bucket.
        """
        return await _ch.list_channels(self._state)

    async def get_channel_history(
        self,
        channel_name: str,
        *,
        limit: int | None = None,
    ) -> tuple[Message, ...]:
        """Get message history for a channel.

        Reads the operator-tuned history batch size and per-batch fetch
        timeout from ``CommunicationBridgeConfig`` when a resolver is
        wired; falls back to the module defaults on settings outage.

        Returns:
            The channel's message history (oldest-first), bounded by
            ``limit`` or the retention default.
        """
        batch_size, fetch_timeout_seconds = await self._resolve_history_params()
        return await _hist.get_channel_history(
            self._state,
            channel_name,
            limit=limit,
            batch_size=batch_size,
            fetch_timeout_seconds=fetch_timeout_seconds,
        )

    async def _resolve_history_params(self) -> tuple[int, float]:
        """Resolve history scan batch size and fetch timeout.

        Reads only the two NATS history settings directly via the
        scalar accessors instead of hydrating the full
        ``CommunicationBridgeConfig`` (which would validate 9 fields
        on every history query). Returns the module defaults
        (100, 0.5) when no resolver is wired or the lookup fails;
        a settings outage must not break history queries.

        Returns:
            A ``(batch_size, fetch_timeout_seconds)`` pair.
        """
        if self._config_resolver is None:
            return 100, 0.5
        namespace = SettingNamespace.COMMUNICATION.value
        try:
            batch_size = await self._config_resolver.get_int(
                namespace, "nats_history_batch_size"
            )
            fetch_timeout = await self._config_resolver.get_float(
                namespace, "nats_history_fetch_timeout_seconds"
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                COMM_BUS_STREAM_SCAN_FAILED,
                phase="resolve_history_params",
                error=("failed to resolve NATS history settings; using scan defaults"),
            )
            return 100, 0.5
        return batch_size, fetch_timeout
