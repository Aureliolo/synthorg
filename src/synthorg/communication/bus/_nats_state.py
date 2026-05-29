"""Shared mutable state for the JetStream message bus submodules.

Each submodule receives a ``_NatsState`` instance rather than the
full ``JetStreamMessageBus`` class, which avoids circular imports
and makes the data dependencies between modules explicit.
"""

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from synthorg.communication.channel import Channel
from synthorg.communication.config import (
    MessageBusConfig,
    NatsConfig,
)
from synthorg.core.clock import Clock, SystemClock

if TYPE_CHECKING:
    from nats.aio.client import Client as NatsClient
    from nats.js import JetStreamContext
    from nats.js.kv import KeyValue

    # PullSubscription is a nested class on JetStreamContext, not a
    # module-level export, so it cannot be imported directly.
    PullSubscription = JetStreamContext.PullSubscription


# Hard deadline on the ``client.drain()`` call inside ``stop()``.
# 30s matches the upstream NATS client's own connection-close grace
# window. Construction-time override via dataclass replace if a
# deployment ever needs to tune it.
DEFAULT_STOP_DRAIN_TIMEOUT_SECONDS: float = 30.0


@dataclass
class _NatsState:
    """Internal mutable state shared across JetStream bus submodules.

    Created by :func:`create_state` and owned by
    ``JetStreamMessageBus``. Submodule functions accept this as their
    first parameter instead of the facade class.
    """

    config: MessageBusConfig
    nats_config: NatsConfig

    # Derived names (computed once at creation).
    stream_name: str
    kv_bucket_name: str

    # NATS primitives (``None`` until connected).
    client: NatsClient | None = None
    js: JetStreamContext | None = None
    kv: KeyValue | None = None

    # Runtime state.
    channels: dict[str, Channel] = field(default_factory=dict)
    subscriptions: dict[tuple[str, str], PullSubscription] = field(default_factory=dict)
    known_agents: set[str] = field(default_factory=set)
    in_flight_fetches: set[asyncio.Task[Any]] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    shutdown_event: asyncio.Event = field(default_factory=asyncio.Event)
    running: bool = False
    # Per docs/reference/lifecycle-sync.md: timed-out stops mark the bus
    # unrestartable. ``start()`` checks this flag and refuses to attach a
    # second listener to the durable consumer left behind by a stuck
    # ``stop()``; recovery is to construct a fresh state via
    # ``create_state``.
    stop_failed: bool = False
    # See ``DEFAULT_STOP_DRAIN_TIMEOUT_SECONDS`` above for the rationale;
    # no per-cluster ``NatsConfig`` field yet because no caller has
    # asked for one.
    stop_drain_timeout_seconds: float = DEFAULT_STOP_DRAIN_TIMEOUT_SECONDS
    # Last time (``time.monotonic`` seconds) a subscriber queue-overflow
    # event was emitted for a given ``(channel, subscriber)``. Used to
    # rate-limit overflow emissions on the NATS receive path so a
    # persistently-paused consumer does not flood logs. Parity with the
    # in-memory bus, where every dropped envelope emits.
    last_overflow_log: dict[tuple[str, str], float] = field(default_factory=dict)

    # Injectable time source. Submodule functions consult ``state.clock``
    # for monotonic deadlines and overflow rate-limit windows so tests
    # can drive virtual time without monkey-patching ``time.monotonic``
    # at module scope.
    clock: Clock = field(default_factory=SystemClock)


def create_state(
    config: MessageBusConfig,
    *,
    clock: Clock | None = None,
) -> _NatsState:
    """Build a ``_NatsState`` from validated bus configuration.

    The caller (``JetStreamMessageBus.__init__``) must ensure
    ``config.nats`` is not ``None`` before calling this function.

    Returns:
        A ``_NatsState`` built from the validated configuration.

    Raises:
        ValueError: If ``config.nats`` is ``None``.
    """
    nats_config = config.nats
    if nats_config is None:  # pragma: no cover -- caller validates
        msg = "config.nats must not be None"
        raise ValueError(msg)
    return _NatsState(
        config=config,
        nats_config=nats_config,
        stream_name=f"{nats_config.stream_name_prefix}_BUS",
        kv_bucket_name=f"{nats_config.stream_name_prefix}_BUS_CHANNELS",
        clock=clock or SystemClock(),
    )
