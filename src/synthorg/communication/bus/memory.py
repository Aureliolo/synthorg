"""In-memory message bus implementation (see Communication design page).

Default backend using asyncio primitives.  Suitable for single-process
deployments and testing.
"""

import asyncio
import contextlib
from collections import deque
from collections.abc import Sequence  # noqa: TC003
from datetime import UTC, datetime
from typing import Final, NoReturn, cast

from synthorg.communication.channel import Channel
from synthorg.communication.config import MessageBusConfig  # noqa: TC001
from synthorg.communication.enums import ChannelType
from synthorg.communication.errors import (
    ChannelAlreadyExistsError,
    ChannelNotFoundError,
    MessageBusAlreadyRunningError,
    MessageBusNotRunningError,
    NotSubscribedError,
)
from synthorg.communication.message import Message  # noqa: TC001
from synthorg.communication.subscription import (
    DeliveryEnvelope,
    Subscription,
)
from synthorg.core.clock import Clock, SystemClock
from synthorg.observability import get_logger
from synthorg.observability.events.communication import (
    COMM_BATCH_PUBLISHED,
    COMM_BUS_ALREADY_RUNNING,
    COMM_BUS_NOT_RUNNING,
    COMM_BUS_SHUTDOWN_SIGNAL,
    COMM_BUS_STARTED,
    COMM_BUS_STOPPED,
    COMM_CHANNEL_ALREADY_EXISTS,
    COMM_CHANNEL_CREATED,
    COMM_CHANNEL_NOT_FOUND,
    COMM_CHANNELS_IDLE_SUMMARY,
    COMM_DIRECT_SENT,
    COMM_HISTORY_QUERIED,
    COMM_MESSAGE_DELIVERED,
    COMM_MESSAGE_PUBLISHED,
    COMM_RECEIVE_SHUTDOWN,
    COMM_RECEIVE_UNSUBSCRIBED,
    COMM_SEND_DIRECT_INVALID,
    COMM_SUBSCRIBER_QUEUE_OVERFLOW,
    COMM_SUBSCRIPTION_CREATED,
    COMM_SUBSCRIPTION_NOT_FOUND,
    COMM_SUBSCRIPTION_REMOVED,
)

logger = get_logger(__name__)

_DM_SEPARATOR = ":"
"""Separator used in deterministic direct-channel names."""

_IDLE_SUMMARY_INTERVAL_SECONDS: Final[float] = 60.0
"""Minimum seconds between idle-channel summary log emissions."""


def _raise_channel_not_found(channel_name: str) -> NoReturn:
    """Log and raise :class:`ChannelNotFoundError`."""
    logger.warning(COMM_CHANNEL_NOT_FOUND, channel=channel_name)
    msg = f"Channel not found: {channel_name}"
    raise ChannelNotFoundError(msg, context={"channel": channel_name})


def _raise_not_subscribed(
    channel_name: str,
    subscriber_id: str,
) -> NoReturn:
    """Log and raise :class:`NotSubscribedError`."""
    logger.warning(
        COMM_SUBSCRIPTION_NOT_FOUND,
        channel=channel_name,
        subscriber=subscriber_id,
    )
    msg = f"Not subscribed to {channel_name}"
    raise NotSubscribedError(
        msg,
        context={
            "channel": channel_name,
            "subscriber": subscriber_id,
        },
    )


class InMemoryMessageBus:
    """In-memory message bus using asyncio queues.

    Implements the :class:`MessageBus` protocol defined in
    ``bus_protocol``.

    Args:
        config: Message bus configuration including pre-defined
            channels and retention settings.
    """

    def __init__(
        self,
        *,
        config: MessageBusConfig,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._clock = clock or SystemClock()
        # Eager init: ``publish`` / ``subscribe`` / ``receive`` may be
        # called before any background lifecycle task runs, so the
        # hot-path bus lock must exist before the first acquire.
        self._lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see above.
        # Per docs/reference/lifecycle-sync.md: serialize start/stop +
        # _running check-and-set under a dedicated lifecycle lock so a
        # concurrent restart cannot race the hot-path mutations.  Hot-
        # path publish/subscribe/receive continue to use ``_lock`` so
        # routine traffic does not serialise against lifecycle calls.
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see.
        self._channels: dict[str, Channel] = {}
        self._queues: dict[tuple[str, str], asyncio.Queue[DeliveryEnvelope | None]] = {}
        self._history: dict[str, deque[Message]] = {}
        self._known_agents: set[str] = set()
        # Per-waiter one-shot futures keyed by (channel, subscriber).
        # ``receive()`` appends a future on entry and removes it on
        # exit; ``unsubscribe()`` pops the set and sets each future so
        # every active waiter wakes up without relying on fitting
        # sentinels into a bounded queue. Using futures (rather than
        # an int counter + sentinel puts) means ``unsubscribe`` never
        # blocks on queue backpressure even if waiter count exceeds
        # ``max_subscriber_queue_size``.
        self._waiters: dict[tuple[str, str], set[asyncio.Future[None]]] = {}
        self._running = False
        # Eager init: ``stop()`` may publish a shutdown signal before
        # any tick has fired; a half-published event would race.
        self._shutdown_event = asyncio.Event()  # lint-allow: loop-bound-init -- see.
        self._idle_poll_count: int = 0
        self._last_idle_summary: float = self._clock.monotonic()

    @property
    def is_running(self) -> bool:
        """Whether the bus client thinks it is running.

        Local state check: flipped by :meth:`start` / :meth:`stop`.
        Tracks the same bit as :meth:`health_check` for the
        in-process backend -- there is no external broker to race.
        """
        return self._running

    async def health_check(self) -> bool:
        """Liveness probe for the in-process bus.

        No external transport to round-trip; the bus is healthy
        when it reports itself running. Async matches the
        :class:`MessageBus` protocol.
        """
        return self._running

    async def start(self) -> None:
        """Start the bus and create pre-configured channels.

        Reinitialises all mutable runtime containers so a restart
        (``start()`` after ``stop()``) produces a fresh state instead
        of leaking stale subscriptions, queued deliveries, or history
        from the previous run.

        Raises:
            MessageBusAlreadyRunningError: If already running.
        """
        async with self._lifecycle_lock:
            if self._running:
                msg = "Message bus is already running"
                logger.warning(COMM_BUS_ALREADY_RUNNING)
                raise MessageBusAlreadyRunningError(msg)
            async with self._lock:
                self._channels.clear()
                self._queues.clear()
                self._history.clear()
                self._known_agents.clear()
                self._waiters.clear()
                self._running = True
                # Allocate a fresh event per generation rather than
                # clearing the existing one. A receive() in flight from
                # the previous run captured the old event; clearing it
                # would let a fast restart resurrect that waiter against
                # the new generation. The new object leaves the old
                # waiter bound to its now-set old event.
                self._shutdown_event = asyncio.Event()  # lint-allow: loop-bound-init
                self._idle_poll_count = 0
                self._last_idle_summary = self._clock.monotonic()
                maxlen = self._config.retention.max_messages_per_channel
                for name in self._config.channels:
                    ch = Channel(name=name, type=ChannelType.TOPIC)
                    self._channels[name] = ch
                    self._history[name] = deque(maxlen=maxlen)
        logger.info(
            COMM_BUS_STARTED,
            channels_created=len(self._config.channels),
        )

    async def stop(self) -> None:
        """Stop the bus gracefully.  Idempotent.

        Signals all pending :meth:`receive` calls to return ``None``.
        """
        async with self._lifecycle_lock:
            if not self._running:
                return
            self._running = False
            # Signal shutdown while still holding the lock so a
            # concurrent start() cannot clear the event between the
            # _running flip and the set(), leaving a running bus stuck
            # in permanent shutdown state.
            self._shutdown_event.set()
            queues_signalled = len(self._queues)
        logger.info(COMM_BUS_STOPPED)
        logger.debug(
            COMM_BUS_SHUTDOWN_SIGNAL,
            queues_signalled=queues_signalled,
        )

    def _require_running(self) -> None:
        """Raise if the bus is not running."""
        if not self._running:
            logger.warning(COMM_BUS_NOT_RUNNING)
            msg = "Message bus is not running"
            raise MessageBusNotRunningError(msg)

    def _ensure_queue(
        self,
        channel_name: str,
        subscriber_id: str,
    ) -> asyncio.Queue[DeliveryEnvelope | None]:
        """Get or create a per-(channel, subscriber) queue.

        Bounded by ``retention.max_subscriber_queue_size`` so a slow
        subscriber cannot leak unbounded memory. Overflow is handled
        by :meth:`_enqueue_or_drop` with a drop-newest policy.
        """
        key = (channel_name, subscriber_id)
        queue = self._queues.get(key)
        if queue is None:
            queue = asyncio.Queue(
                maxsize=self._config.retention.max_subscriber_queue_size,
            )
            self._queues[key] = queue
        return queue

    def _enqueue_or_drop(
        self,
        queue: asyncio.Queue[DeliveryEnvelope | None],
        envelope: DeliveryEnvelope,
        *,
        channel_name: str,
        subscriber_id: str,
    ) -> bool:
        """Enqueue an envelope or drop it (newest) on overflow.

        Emits ``COMM_SUBSCRIBER_QUEUE_OVERFLOW`` at WARNING when the
        subscriber's queue is full so operators can tell the difference
        between ``receive`` returning ``None`` on shutdown vs. messages
        being silently dropped upstream.

        Returns ``True`` on successful enqueue, ``False`` when the
        envelope was dropped. Callers gate ``COMM_MESSAGE_DELIVERED``
        emission on the return value so a dropped envelope is never
        logged as delivered.
        """
        try:
            queue.put_nowait(envelope)
        except asyncio.QueueFull:
            logger.warning(
                COMM_SUBSCRIBER_QUEUE_OVERFLOW,
                channel=channel_name,
                subscriber=subscriber_id,
                queue_size=queue.maxsize,
                drop_policy="newest",
                backend="memory",
                message_id=str(envelope.message.id),
            )
            return False
        return True

    async def publish(
        self,
        message: Message,
        *,
        ttl_seconds: float | None = None,  # noqa: ARG002
    ) -> None:
        """Publish a message to its channel.

        Args:
            message: The message to publish.
            ttl_seconds: Accepted for protocol conformance but
                ignored (in-memory bus uses deque-based retention).

        Raises:
            MessageBusNotRunningError: If not running.
            ChannelNotFoundError: If the channel does not exist.
        """
        async with self._lock:
            self._require_running()
            channel_name = message.channel
            if channel_name not in self._channels:
                _raise_channel_not_found(channel_name)
            channel = self._channels[channel_name]
            self._history[channel_name].append(message)
            now = datetime.now(UTC)
            if channel.type == ChannelType.BROADCAST:
                targets = self._known_agents
            else:
                targets = set(channel.subscribers)
            for sub_id in targets:
                queue = self._ensure_queue(channel_name, sub_id)
                envelope = DeliveryEnvelope(
                    message=message,
                    channel_name=channel_name,
                    delivered_at=now,
                )
                # Gate the delivery log on actual enqueue success;
                # a dropped envelope must not be counted as delivered.
                if self._enqueue_or_drop(
                    queue,
                    envelope,
                    channel_name=channel_name,
                    subscriber_id=sub_id,
                ):
                    logger.debug(
                        COMM_MESSAGE_DELIVERED,
                        channel=channel_name,
                        subscriber=sub_id,
                        message_id=str(message.id),
                    )
        logger.info(
            COMM_MESSAGE_PUBLISHED,
            channel=channel_name,
            message_id=str(message.id),
            type=str(message.type),
        )

    async def send_direct(
        self,
        message: Message,
        *,
        recipient: str,
        ttl_seconds: float | None = None,  # noqa: ARG002
    ) -> None:
        """Send a direct message between two agents.

        Lazily creates a DIRECT channel named ``@{a}:{b}`` (where
        a, b are the sorted agent IDs) and subscribes both agents.

        Args:
            message: The message to send.
            recipient: The recipient agent ID.
            ttl_seconds: Accepted for protocol conformance but
                ignored (in-memory bus uses deque-based retention).

        Raises:
            MessageBusNotRunningError: If not running.
            ValueError: If *recipient* does not match ``message.to``,
                or if agent IDs contain the separator character.
        """
        sender = message.sender
        if message.to != recipient:
            msg = f"recipient={recipient!r} does not match message.to={message.to!r}"
            logger.warning(
                COMM_SEND_DIRECT_INVALID,
                error=msg,
            )
            raise ValueError(msg)
        for agent_id in (sender, recipient):
            if _DM_SEPARATOR in agent_id:
                msg = (
                    f"Agent ID {agent_id!r} contains the reserved "
                    f"separator character {_DM_SEPARATOR!r}"
                )
                logger.warning(
                    COMM_SEND_DIRECT_INVALID,
                    error=msg,
                )
                raise ValueError(msg)
        a, b = sorted([sender, recipient])
        pair = (a, b)
        channel_name = f"@{pair[0]}:{pair[1]}"
        async with self._lock:
            self._require_running()
            self._ensure_direct_channel(channel_name, pair)
            self._deliver_to_pair(channel_name, pair, message)
        logger.info(
            COMM_DIRECT_SENT,
            channel=channel_name,
            sender=sender,
            recipient=recipient,
            message_id=str(message.id),
        )

    async def publish_batch(
        self,
        messages: Sequence[Message],
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        """Publish multiple messages sequentially.

        In-memory implementation publishes each message in order.
        ``ttl_seconds`` is accepted for protocol conformance but
        ignored (retention is deque-based).

        If any individual publish fails, the remaining messages in the
        batch are not attempted and previously published messages are
        not rolled back.

        Args:
            messages: Messages to publish.
            ttl_seconds: Ignored (protocol conformance).

        Raises:
            MessageBusNotRunningError: If the bus is not running.
            ChannelNotFoundError: If any target channel does not exist.
        """
        if not messages:
            return
        for message in messages:
            await self.publish(message, ttl_seconds=ttl_seconds)
        logger.debug(
            COMM_BATCH_PUBLISHED,
            count=len(messages),
            backend="memory",
        )

    def _ensure_direct_channel(
        self,
        channel_name: str,
        pair: tuple[str, str],
    ) -> None:
        """Create DIRECT channel and register agents if needed.

        Must be called under ``self._lock``.
        """
        if channel_name not in self._channels:
            ch = Channel(
                name=channel_name,
                type=ChannelType.DIRECT,
                subscribers=pair,
            )
            self._channels[channel_name] = ch
            maxlen = self._config.retention.max_messages_per_channel
            self._history[channel_name] = deque(maxlen=maxlen)
            logger.info(
                COMM_CHANNEL_CREATED,
                channel=channel_name,
                type=str(ChannelType.DIRECT),
            )
        for agent_id in pair:
            self._known_agents.add(agent_id)
            self._ensure_queue(channel_name, agent_id)
        current_ch = self._channels[channel_name]
        current_subs = set(current_ch.subscribers)
        pair_set = set(pair)
        if not pair_set.issubset(current_subs):
            new_subs = tuple(sorted(current_subs | pair_set))
            self._channels[channel_name] = current_ch.model_copy(
                update={"subscribers": new_subs},
            )

    def _deliver_to_pair(
        self,
        channel_name: str,
        pair: tuple[str, str],
        message: Message,
    ) -> None:
        """Append to history and enqueue for both agents.

        Must be called under ``self._lock``.
        """
        self._history[channel_name].append(message)
        now = datetime.now(UTC)
        for agent_id in pair:
            envelope = DeliveryEnvelope(
                message=message,
                channel_name=channel_name,
                delivered_at=now,
            )
            if self._enqueue_or_drop(
                self._queues[(channel_name, agent_id)],
                envelope,
                channel_name=channel_name,
                subscriber_id=agent_id,
            ):
                logger.debug(
                    COMM_MESSAGE_DELIVERED,
                    channel=channel_name,
                    subscriber=agent_id,
                    message_id=str(message.id),
                )

    async def subscribe(
        self,
        channel_name: str,
        subscriber_id: str,
    ) -> Subscription:
        """Subscribe an agent to a channel.

        Idempotent -- returns a fresh subscription record if already
        subscribed (the channel's subscriber list is not duplicated).

        Args:
            channel_name: Channel to subscribe to.
            subscriber_id: Agent ID of the subscriber.

        Returns:
            The subscription record.

        Raises:
            MessageBusNotRunningError: If not running.
            ChannelNotFoundError: If the channel does not exist.
        """
        async with self._lock:
            self._require_running()
            if channel_name not in self._channels:
                _raise_channel_not_found(channel_name)
            self._known_agents.add(subscriber_id)
            channel = self._channels[channel_name]
            if subscriber_id in channel.subscribers:
                return Subscription(
                    channel_name=channel_name,
                    subscriber_id=subscriber_id,
                    subscribed_at=datetime.now(UTC),
                )
            new_subs = (*channel.subscribers, subscriber_id)
            self._channels[channel_name] = channel.model_copy(
                update={"subscribers": new_subs},
            )
            self._ensure_queue(channel_name, subscriber_id)
        now = datetime.now(UTC)
        logger.info(
            COMM_SUBSCRIPTION_CREATED,
            channel=channel_name,
            subscriber=subscriber_id,
        )
        return Subscription(
            channel_name=channel_name,
            subscriber_id=subscriber_id,
            subscribed_at=now,
        )

    async def unsubscribe(
        self,
        channel_name: str,
        subscriber_id: str,
    ) -> None:
        """Remove an agent's subscription from a channel.

        Args:
            channel_name: Channel to unsubscribe from.
            subscriber_id: Agent ID to remove.

        Raises:
            MessageBusNotRunningError: If not running.
            NotSubscribedError: If the agent is not subscribed.
        """
        waiters: set[asyncio.Future[None]] = set()
        async with self._lock:
            self._require_running()
            if channel_name not in self._channels:
                _raise_not_subscribed(channel_name, subscriber_id)
            channel = self._channels[channel_name]
            if subscriber_id not in channel.subscribers:
                _raise_not_subscribed(channel_name, subscriber_id)
            new_subs = tuple(s for s in channel.subscribers if s != subscriber_id)
            self._channels[channel_name] = channel.model_copy(
                update={"subscribers": new_subs},
            )
            key = (channel_name, subscriber_id)
            self._queues.pop(key, None)
            waiters = self._waiters.pop(key, set())
        # Wake every pending ``receive()`` by resolving its per-waiter
        # future. No sentinel puts, no bounded-queue backpressure:
        # ``unsubscribe()`` cannot block regardless of how many
        # waiters are active. ``set_result(None)`` on an already-done
        # future is illegal, so guard against the rare case where a
        # concurrent shutdown / timeout already resolved the future.
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(None)
        logger.info(
            COMM_SUBSCRIPTION_REMOVED,
            channel=channel_name,
            subscriber=subscriber_id,
        )

    async def receive(
        self,
        channel_name: str,
        subscriber_id: str,
        *,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> DeliveryEnvelope | None:
        """Receive the next message from a channel.

        Awaits until a message is available, the timeout expires, or
        the bus is stopped.  When ``timeout`` is ``None``, awaits
        indefinitely (or until shutdown).

        Args:
            channel_name: Channel to receive from.
            subscriber_id: Agent ID receiving.
            timeout: Seconds to wait before returning ``None``.

        Returns:
            The next delivery envelope, or ``None`` when:

            - *timeout* expires without a message arriving.
            - The bus is shut down while waiting.
            - The subscription is cancelled via :meth:`unsubscribe`
              while a ``receive()`` call is in flight.

        Raises:
            MessageBusNotRunningError: If the bus is not running.
            ChannelNotFoundError: If the channel does not exist.
            NotSubscribedError: If the subscriber is not subscribed
                (for TOPIC and DIRECT channels).
        """
        unsub_future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        async with self._lock:
            self._require_running()
            if channel_name not in self._channels:
                _raise_channel_not_found(channel_name)
            channel = self._channels[channel_name]
            if (
                channel.type != ChannelType.BROADCAST
                and subscriber_id not in channel.subscribers
            ):
                _raise_not_subscribed(channel_name, subscriber_id)
            queue = self._ensure_queue(channel_name, subscriber_id)
            key = (channel_name, subscriber_id)
            self._waiters.setdefault(key, set()).add(unsub_future)
            # Bind to this generation's shutdown event while holding
            # the lock. A concurrent stop()+start() swaps in a fresh
            # event; capturing here ties this waiter to the event
            # stop() will actually set, so a fast restart cannot strand
            # it on a stale generation.
            shutdown_event = self._shutdown_event
        try:
            result = await self._await_with_shutdown(
                queue, timeout, unsub_future, shutdown_event
            )
        finally:
            # Remove this waiter's future from the active set so the
            # next ``unsubscribe`` only targets still-live waiters.
            #
            # Safety: asyncio is single-threaded; the ``get`` /
            # ``discard`` / ``pop`` sequence below has no ``await``
            # between operations, so no other coroutine can run in
            # the gap and observe a half-cleared entry. The asymmetry
            # with the lock-guarded add at line 627 is deliberate:
            # the add must happen before the await inside
            # ``_await_with_shutdown`` reaches the lock-held block on
            # the producer side, so it pays for the lock; the remove
            # only needs to land before the next ``unsubscribe`` runs,
            # and asyncio's run-to-completion guarantee delivers that
            # for free. If this bus is ever migrated to real threads,
            # the discard must move inside ``async with self._lock``.
            active = self._waiters.get(key)
            if active is not None:
                active.discard(unsub_future)
                if not active:
                    self._waiters.pop(key, None)
        if result is None:
            await self._log_receive_null(channel_name, subscriber_id)
        return result

    async def _log_receive_null(
        self,
        channel_name: str,
        subscriber_id: str,
    ) -> None:
        """Log the cause when ``receive()`` returns ``None``.

        Acquires the lock to safely inspect bus state (queue map
        and shutdown flag) so the inferred reason is not racy.
        For normal idle timeouts, increments a counter and emits
        a periodic summary instead of per-timeout spam.
        """
        async with self._lock:
            is_shutdown = self._shutdown_event.is_set()
            is_unsubscribed = (channel_name, subscriber_id) not in self._queues
        if is_shutdown:
            logger.debug(
                COMM_RECEIVE_SHUTDOWN,
                channel=channel_name,
                subscriber=subscriber_id,
            )
        elif is_unsubscribed:
            logger.debug(
                COMM_RECEIVE_UNSUBSCRIBED,
                channel=channel_name,
                subscriber=subscriber_id,
            )
        else:
            self._idle_poll_count += 1
            now = self._clock.monotonic()
            if now - self._last_idle_summary >= _IDLE_SUMMARY_INTERVAL_SECONDS:
                logger.debug(
                    COMM_CHANNELS_IDLE_SUMMARY,
                    idle_polls=self._idle_poll_count,
                    subscriber_count=len(self._queues),
                    interval_seconds=round(now - self._last_idle_summary, 1),
                )
                self._idle_poll_count = 0
                self._last_idle_summary = now

    async def _await_with_shutdown(
        self,
        queue: asyncio.Queue[DeliveryEnvelope | None],
        timeout: float | None,  # noqa: ASYNC109
        unsub_future: asyncio.Future[None],
        shutdown_event: asyncio.Event,
    ) -> DeliveryEnvelope | None:
        """Await next envelope, returning ``None`` on timeout, shutdown, or unsubscribe.

        Args:
            queue: The subscriber's delivery queue.
            timeout: Seconds to wait (``None`` = indefinitely).
            unsub_future: Per-waiter one-shot future that ``unsubscribe()``
                resolves to wake this receive. Resolving the future is
                how the caller cancels an in-flight receive without
                needing to send a sentinel through the bounded queue.
            shutdown_event: The generation-scoped shutdown event the
                caller captured under ``self._lock``. Awaiting this
                captured reference (not ``self._shutdown_event``) keeps
                the waiter bound to the event ``stop()`` will set, even
                if a concurrent restart swaps in a new one.

        Returns:
            The next envelope, or ``None``.
        """
        get_task = asyncio.create_task(queue.get())
        shutdown_task = asyncio.create_task(
            shutdown_event.wait(),
        )
        # ``asyncio.wait`` requires awaitables of the same type. Cast
        # the heterogeneous {get_task, shutdown_task, unsub_future} set
        # through ``asyncio.Future[object]`` so the type checker is
        # happy; at runtime all three are valid awaitables for wait().
        wait_set: set[asyncio.Future[object]] = {
            cast("asyncio.Future[object]", get_task),
            cast("asyncio.Future[object]", shutdown_task),
            cast("asyncio.Future[object]", unsub_future),
        }
        try:
            done, _ = await asyncio.wait(
                wait_set,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            get_task.cancel()
            shutdown_task.cancel()
            raise
        if not get_task.done():
            get_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await get_task
        if not shutdown_task.done():
            shutdown_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await shutdown_task
        # ``unsub_future`` is owned by ``receive()``; do not cancel it
        # here. If it is not in ``done`` it stays pending for the
        # remainder of the receive -- ``receive()``'s ``finally``
        # removes it from ``_waiters`` regardless.
        if get_task in done and not get_task.cancelled():
            return get_task.result()
        return None

    async def create_channel(self, channel: Channel) -> Channel:
        """Create a new channel.

        Args:
            channel: Channel definition to create.

        Returns:
            The created channel.

        Raises:
            MessageBusNotRunningError: If not running.
            ChannelAlreadyExistsError: If already exists.
        """
        async with self._lock:
            self._require_running()
            if channel.name in self._channels:
                logger.warning(
                    COMM_CHANNEL_ALREADY_EXISTS,
                    channel=channel.name,
                )
                msg = f"Channel already exists: {channel.name}"
                raise ChannelAlreadyExistsError(
                    msg,
                    context={"channel": channel.name},
                )
            self._channels[channel.name] = channel
            maxlen = self._config.retention.max_messages_per_channel
            self._history[channel.name] = deque(maxlen=maxlen)
        logger.info(
            COMM_CHANNEL_CREATED,
            channel=channel.name,
            type=str(channel.type),
        )
        return channel

    async def get_channel(self, channel_name: str) -> Channel:
        """Get a channel by name.

        Args:
            channel_name: Name of the channel.

        Returns:
            The channel.

        Raises:
            ChannelNotFoundError: If the channel does not exist.
        """
        async with self._lock:
            if channel_name not in self._channels:
                _raise_channel_not_found(channel_name)
            return self._channels[channel_name]

    async def list_channels(self) -> tuple[Channel, ...]:
        """List all channels.

        Returns:
            All registered channels.
        """
        async with self._lock:
            return tuple(self._channels.values())

    async def get_channel_history(
        self,
        channel_name: str,
        *,
        limit: int | None = None,
    ) -> tuple[Message, ...]:
        """Get message history for a channel.

        Args:
            channel_name: Channel to query.
            limit: Maximum number of most recent messages to return.
                Values ``<= 0`` return an empty tuple.

        Returns:
            Messages in chronological order.

        Raises:
            ChannelNotFoundError: If the channel does not exist.
        """
        async with self._lock:
            if channel_name not in self._channels:
                _raise_channel_not_found(channel_name)
            messages = list(self._history[channel_name])
        if limit is not None:
            if limit <= 0:
                messages = []
            elif limit < len(messages):
                messages = messages[-limit:]
        logger.debug(
            COMM_HISTORY_QUERIED,
            channel=channel_name,
            count=len(messages),
            limit=limit,
        )
        return tuple(messages)
