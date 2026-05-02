"""Factories for the escalation queue backend and decision processor.

Both factories dispatch via small registry maps so adding a new backend
or decision strategy is a single registry entry rather than a new
branch in an if/elif chain. The shape mirrors
``synthorg.persistence.registry.PersistenceBackendRegistry`` and the
``match/case`` dispatch in ``synthorg.communication.bus``.
"""

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.communication.conflict_resolution.escalation.config import (
    EscalationQueueConfig,
)
from synthorg.communication.conflict_resolution.escalation.in_memory_store import (
    InMemoryEscalationStore,
)
from synthorg.communication.conflict_resolution.escalation.notify import (
    EscalationNotifySubscriber,
    NoopEscalationNotifySubscriber,
)
from synthorg.communication.conflict_resolution.escalation.processors import (
    HybridDecisionProcessor,
    WinnerSelectProcessor,
)
from synthorg.communication.conflict_resolution.escalation.protocol import (
    DecisionProcessor,
    EscalationQueueStore,
)
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

if TYPE_CHECKING:
    from synthorg.communication.conflict_resolution.escalation.registry import (
        PendingFuturesRegistry,
    )
    from synthorg.persistence.protocol import PersistenceBackend


logger = get_logger(__name__)


type _QueueStoreFactory = Callable[
    [EscalationQueueConfig, "PersistenceBackend | None"],
    EscalationQueueStore,
]
type _DecisionProcessorFactory = Callable[[], DecisionProcessor]


def _require_persistence(
    config_backend: str,
    persistence: PersistenceBackend | None,
) -> PersistenceBackend:
    """Reject a missing or mismatched persistence backend, logging before raise.

    The escalation queue store's backend (``memory`` / ``sqlite`` /
    ``postgres``) MUST line up with the operator's persistence choice.
    A mismatch -- typically because someone hand-injected a backend
    instance that does not match the configuration -- is surfaced at
    construction time rather than allowed to silently fall back to an
    in-memory state or to interact with the wrong driver.
    """
    if persistence is None:
        msg = f"{config_backend} backend requires a connected persistence backend"
        logger.warning(
            API_APP_STARTUP,
            component="escalation_factory",
            error=msg,
            config_backend=config_backend,
        )
        raise ValueError(msg)
    actual_backend = str(persistence.backend_name)
    if actual_backend != config_backend:
        msg = (
            f"config.backend={config_backend!r} but persistence backend is "
            f"{actual_backend!r}"
        )
        logger.warning(
            API_APP_STARTUP,
            component="escalation_factory",
            error=msg,
            config_backend=config_backend,
            actual_backend=actual_backend,
        )
        raise ValueError(msg)
    return persistence


def _build_memory_store(
    config: EscalationQueueConfig,
    persistence: PersistenceBackend | None,
) -> EscalationQueueStore:
    del config, persistence
    return InMemoryEscalationStore()


def _build_sqlite_store(
    config: EscalationQueueConfig,
    persistence: PersistenceBackend | None,
) -> EscalationQueueStore:
    del config
    backend = _require_persistence("sqlite", persistence)
    return backend.build_escalations()


def _build_postgres_store(
    config: EscalationQueueConfig,
    persistence: PersistenceBackend | None,
) -> EscalationQueueStore:
    backend = _require_persistence("postgres", persistence)
    # Pass the notify channel only when cross-instance notify is
    # enabled so the repo's NOTIFY publishing is a true no-op for
    # single-worker deployments.
    notify_channel: str | None = None
    if config.cross_instance_notify in {"auto", "on"}:
        notify_channel = config.notify_channel
    return backend.build_escalations(notify_channel=notify_channel)


_QUEUE_STORE_FACTORIES: Mapping[str, _QueueStoreFactory] = MappingProxyType(
    {
        "memory": _build_memory_store,
        "sqlite": _build_sqlite_store,
        "postgres": _build_postgres_store,
    },
)


def build_escalation_queue_store(
    config: EscalationQueueConfig,
    persistence: PersistenceBackend | None = None,
) -> EscalationQueueStore:
    """Construct the configured :class:`EscalationQueueStore`.

    Args:
        config: Queue backend selection and tuning.
        persistence: Live persistence backend.  Required when
            ``config.backend`` is ``sqlite`` or ``postgres``.

    Returns:
        A concrete :class:`EscalationQueueStore` implementation.

    Raises:
        ValueError: ``backend`` is ``sqlite`` or ``postgres`` but the
            persistence backend is missing or of a mismatched type, or
            ``backend`` is not a registered key.
    """
    factory = _QUEUE_STORE_FACTORIES.get(config.backend)
    if factory is None:
        available = sorted(_QUEUE_STORE_FACTORIES) or ["(none)"]
        msg = (
            f"Unknown escalation queue backend: {config.backend!r}. "
            f"Registered backends: {', '.join(available)}"
        )
        raise ValueError(msg)
    return factory(config, persistence)


def build_escalation_notify_subscriber(
    config: EscalationQueueConfig,
    store: EscalationQueueStore,
    registry: PendingFuturesRegistry,
    *,
    reconnect_delay_seconds: float,
) -> EscalationNotifySubscriber:
    """Construct the cross-instance notify subscriber for the queue.

    Returns a no-op subscriber unless the backend is Postgres and
    ``cross_instance_notify`` is enabled (``auto`` or ``on``).  The
    subscriber forwards state-transition NOTIFY payloads to the local
    :class:`PendingFuturesRegistry` so resolvers on other workers
    wake immediately instead of waiting for their timeout.

    Args:
        config: Queue configuration.
        store: The store built by :func:`build_escalation_queue_store`.
            When it is a ``PostgresEscalationRepository`` and
            ``cross_instance_notify`` is enabled, the subscriber reuses
            its pool for LISTEN.
        registry: Process-local future registry to signal.
        reconnect_delay_seconds: Delay before reconnecting after a
            connection drop.  Resolve via
            ``ConfigResolver.get_float("communication",
            "escalation_subscriber_reconnect_delay_seconds")`` at the
            call site.

    Returns:
        A concrete :class:`EscalationNotifySubscriber`.  Callers must
        ``await subscriber.start()`` during app startup and
        ``await subscriber.stop()`` during shutdown.

    Raises:
        ValueError: ``cross_instance_notify="on"`` but the backend is
            not Postgres -- surfaces misconfiguration at startup.
    """
    mode = config.cross_instance_notify
    if mode == "off":
        return NoopEscalationNotifySubscriber()
    if config.backend != "postgres":
        if mode == "on":
            msg = (
                "cross_instance_notify='on' requires backend='postgres'; "
                f"got backend={config.backend!r}."
            )
            raise ValueError(msg)
        return NoopEscalationNotifySubscriber()
    # Local import to avoid a hard dependency on psycopg when the
    # backend is not actually Postgres.
    from synthorg.communication.conflict_resolution.escalation.notify import (  # noqa: PLC0415
        PostgresEscalationNotifySubscriber,
    )
    from synthorg.persistence.postgres.escalation_repo import (  # noqa: PLC0415
        PostgresEscalationRepository,
    )

    if not isinstance(store, PostgresEscalationRepository):
        # Defensive: in principle factory-built stores and the backend
        # discriminator match; a mismatch means someone hand-injected
        # the store.
        return NoopEscalationNotifySubscriber()
    return PostgresEscalationNotifySubscriber(
        store,
        registry,
        channel=config.notify_channel,
        reconnect_delay_seconds=reconnect_delay_seconds,
    )


_DECISION_PROCESSOR_FACTORIES: Mapping[str, _DecisionProcessorFactory] = (
    MappingProxyType(
        {
            "winner": WinnerSelectProcessor,
            "hybrid": HybridDecisionProcessor,
        },
    )
)


def build_decision_processor(
    config: EscalationQueueConfig,
) -> DecisionProcessor:
    """Construct the configured :class:`DecisionProcessor`.

    Args:
        config: Queue configuration.

    Returns:
        The concrete decision processor selected by
        ``config.decision_strategy``.

    Raises:
        ValueError: ``decision_strategy`` is not a registered key.
    """
    factory = _DECISION_PROCESSOR_FACTORIES.get(config.decision_strategy)
    if factory is None:
        available = sorted(_DECISION_PROCESSOR_FACTORIES) or ["(none)"]
        msg = (
            f"Unknown decision_strategy: {config.decision_strategy!r}. "
            f"Registered strategies: {', '.join(available)}"
        )
        raise ValueError(msg)
    return factory()
