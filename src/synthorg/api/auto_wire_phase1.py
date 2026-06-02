# module-kind: code
"""Construction-time phase-1 service auto-wiring.

Creates the services that do not need a connected persistence backend --
message bus, cost tracker, provider registry, task engine (+ optional
distributed dispatcher), and provider health tracker -- returning them in a
:class:`Phase1Result`. Split out of ``api.auto_wire`` so each auto-wire group
stays under the module-size budget.
"""

from typing import TYPE_CHECKING, NamedTuple

from synthorg.api.channels import ALL_CHANNELS
from synthorg.budget.tracker import CostTracker
from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.config import NatsConfig
from synthorg.config.schema import RootConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_SERVICE_AUTO_WIRED,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.cassette import CassetteConfig
from synthorg.providers.health import ProviderHealthTracker
from synthorg.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    # The distributed task-queue stack lives behind the optional
    # ``synthorg[distributed]`` extra and is imported lazily (with
    # ``ImportError`` handling) inside ``_register_distributed_dispatcher``;
    # ``workers.config`` additionally sits in a genuine import cycle. These
    # stay type-only so the module imports without the extra installed.
    from synthorg.workers.backend_services import DistributedBackendServices
    from synthorg.workers.claim import JetStreamTaskQueue
    from synthorg.workers.config import QueueConfig
    from synthorg.workers.dispatcher import DistributedDispatcher

logger = get_logger(__name__)


class Phase1Result(NamedTuple):
    """Services created during construction-time auto-wiring."""

    message_bus: MessageBus | None
    cost_tracker: CostTracker | None
    task_engine: TaskEngine | None
    provider_registry: ProviderRegistry | None
    provider_health_tracker: ProviderHealthTracker | None
    distributed_task_queue: JetStreamTaskQueue | None
    distributed_dispatcher: DistributedDispatcher | None
    distributed_backend_services: DistributedBackendServices | None


def auto_wire_phase1(  # noqa: PLR0913
    *,
    effective_config: RootConfig,
    persistence: PersistenceBackend | None,
    message_bus: MessageBus | None,
    cost_tracker: CostTracker | None,
    task_engine: TaskEngine | None,
    provider_registry: ProviderRegistry | None,
    provider_health_tracker: ProviderHealthTracker | None,
) -> Phase1Result:
    """Auto-wire services that don't need connected persistence.

    Each service is created only when the caller passes ``None``. Explicit
    values are preserved unchanged.

    Args:
        effective_config: Root company configuration.
        persistence: Persistence backend (may be ``None``). When ``None``,
            ``task_engine`` cannot be auto-wired and a warning is logged.
        message_bus: Explicit bus or ``None`` to auto-wire.
        cost_tracker: Explicit tracker or ``None`` to auto-wire.
        task_engine: Explicit engine or ``None`` to auto-wire.
        provider_registry: Explicit registry or ``None`` to auto-wire.
        provider_health_tracker: Explicit tracker or ``None`` to auto-wire.

    Returns:
        A ``Phase1Result`` with all (possibly auto-wired) services.
    """
    distributed_task_queue: JetStreamTaskQueue | None = None
    distributed_dispatcher: DistributedDispatcher | None = None
    distributed_backend_services: DistributedBackendServices | None = None

    if message_bus is None:
        message_bus = _auto_wire_message_bus(effective_config)

    if cost_tracker is None:
        cost_tracker = _wire_cost_tracker(effective_config)

    if provider_registry is None and effective_config.providers:
        provider_registry = _wire_provider_registry(effective_config)

    if task_engine is None and persistence is not None:
        (
            task_engine,
            distributed_task_queue,
            distributed_dispatcher,
            distributed_backend_services,
        ) = _wire_task_engine(
            persistence,
            message_bus,
            queue_config=effective_config.queue,
            nats_config=effective_config.communication.message_bus.nats,
        )

    if provider_health_tracker is None:
        provider_health_tracker = ProviderHealthTracker()
        logger.info(API_SERVICE_AUTO_WIRED, service="provider_health_tracker")

    if persistence is None:
        logger.warning(
            API_APP_STARTUP,
            note=(
                "No persistence backend available (SYNTHORG_DB_PATH not set) "
                "-- persistence-dependent services (task_engine, "
                "settings_service) will not be auto-wired; affected "
                "controllers will return 503"
            ),
        )

    return Phase1Result(
        message_bus=message_bus,
        cost_tracker=cost_tracker,
        task_engine=task_engine,
        provider_registry=provider_registry,
        provider_health_tracker=provider_health_tracker,
        distributed_task_queue=distributed_task_queue,
        distributed_dispatcher=distributed_dispatcher,
        distributed_backend_services=distributed_backend_services,
    )


def _wire_cost_tracker(effective_config: RootConfig) -> CostTracker:
    """Create a CostTracker from config.

    Returns:
        The configured cost tracker.
    """
    try:
        tracker = CostTracker(budget_config=effective_config.budget)
    except Exception as exc:
        log_exception_redacted(
            logger, API_APP_STARTUP, exc, note="Failed to auto-wire cost tracker"
        )
        raise
    logger.info(API_SERVICE_AUTO_WIRED, service="cost_tracker")
    return tracker


def resolve_cassette_config() -> CassetteConfig | None:
    """Resolve the boot-time cassette config (Cat-2: env > default).

    Uses the sanctioned pre-init bootstrap resolver -- no ``os.environ`` read
    in provider code.

    Returns:
        The resolved cassette config, or ``None`` when the seam is inert so the
        registry holds the concrete drivers unchanged.
    """
    from pathlib import Path  # noqa: PLC0415

    from synthorg.providers.cassette import (  # noqa: PLC0415
        CassetteConfig,
        CassetteMode,
    )
    from synthorg.settings.bootstrap_resolver import (  # noqa: PLC0415
        resolve_init_value,
    )
    from synthorg.settings.enums import SettingNamespace  # noqa: PLC0415

    mode_raw = str(
        resolve_init_value(SettingNamespace.PROVIDERS, "cassette_mode").value
    ).strip()
    mode = CassetteMode(mode_raw)
    if mode is CassetteMode.OFF:
        return None
    path_resolved = resolve_init_value(
        SettingNamespace.PROVIDERS, "cassette_path"
    ).value
    path = Path(str(path_resolved)) if path_resolved else None
    return CassetteConfig(mode=mode, path=path)


def _wire_provider_registry(
    effective_config: RootConfig,
) -> ProviderRegistry:
    """Create a ProviderRegistry from config.

    Returns:
        The configured provider registry.
    """
    try:
        registry = ProviderRegistry.from_config(
            effective_config.providers,
            cassette=resolve_cassette_config(),
        )
    except Exception as exc:
        log_exception_redacted(
            logger,
            API_APP_STARTUP,
            exc,
            note="Failed to build provider registry from config",
        )
        raise
    logger.info(API_SERVICE_AUTO_WIRED, service="provider_registry")
    return registry


def _wire_task_engine(
    persistence: PersistenceBackend,
    message_bus: MessageBus | None,
    queue_config: QueueConfig | None = None,
    nats_config: NatsConfig | None = None,
) -> tuple[
    TaskEngine,
    JetStreamTaskQueue | None,
    DistributedDispatcher | None,
    DistributedBackendServices | None,
]:
    """Create a TaskEngine from persistence and optional bus.

    When ``queue_config.enabled`` is true, also create a
    :class:`JetStreamTaskQueue` and register a :class:`DistributedDispatcher`
    observer so task state changes are published to the distributed work
    queue. The caller owns the returned task queue's async lifecycle.

    Returns:
        A ``(task_engine, task_queue, dispatcher, backend_services)`` tuple.
        The last three are non-``None`` only when ``queue_config.enabled`` is
        true, the ``nats_config`` is present, and ``synthorg[distributed]`` is
        installed; otherwise all three are ``None`` and the in-process path is
        used. The dispatcher is returned so the API startup hook can late-bind
        its live ``WorkersBridgeConfig`` provider once ``AppState`` exists.
    """
    try:
        engine = TaskEngine(
            persistence=persistence,
            message_bus=message_bus,
        )
    except Exception as exc:
        log_exception_redacted(
            logger, API_APP_STARTUP, exc, note="Failed to auto-wire task engine"
        )
        raise

    task_queue: JetStreamTaskQueue | None = None
    dispatcher: DistributedDispatcher | None = None
    backend_services: DistributedBackendServices | None = None
    if queue_config is not None and queue_config.enabled:
        if nats_config is None:
            logger.warning(
                API_APP_STARTUP,
                note=(
                    "queue.enabled is true but nats config is missing; "
                    "distributed dispatcher will not be registered"
                ),
            )
        else:
            task_queue, dispatcher, backend_services = _register_distributed_dispatcher(
                engine,
                queue_config,
                nats_config,
                persistence,
            )

    logger.info(API_SERVICE_AUTO_WIRED, service="task_engine")
    return engine, task_queue, dispatcher, backend_services


def _register_distributed_dispatcher(
    engine: TaskEngine,
    queue_config: QueueConfig,
    nats_config: NatsConfig,
    persistence: PersistenceBackend,
) -> tuple[
    JetStreamTaskQueue | None,
    DistributedDispatcher | None,
    DistributedBackendServices | None,
]:
    """Register the distributed dispatcher observer on the task engine.

    Creates a :class:`JetStreamTaskQueue` (not started), a
    :class:`DistributedDispatcher` observer, and the backend distributed-path
    service bundle. Registration is best-effort: any failure here is logged
    but does not abort startup, because the in-process path remains viable.

    Returns:
        The constructed ``(queue, dispatcher, backend_services)``, or
        ``(None, None, None)`` when the optional ``synthorg[distributed]``
        dependency is missing or construction itself fails.
    """
    try:
        from synthorg.workers.backend_services import (  # noqa: PLC0415
            build_distributed_backend_services,
        )
        from synthorg.workers.claim import (  # noqa: PLC0415
            JetStreamTaskQueue,
        )
        from synthorg.workers.dispatcher import (  # noqa: PLC0415
            DistributedDispatcher,
        )
    except ImportError:
        logger.warning(
            API_APP_STARTUP,
            note=(
                "queue.enabled is true but 'synthorg[distributed]' is not "
                "installed; distributed dispatcher will not be registered"
            ),
        )
        return None, None, None

    try:
        task_queue = JetStreamTaskQueue(
            queue_config=queue_config,
            nats_config=nats_config,
        )
        dispatcher = DistributedDispatcher(task_queue=task_queue)
        backend_services = build_distributed_backend_services(
            task_queue=task_queue,
            engine=engine,
            queue_config=queue_config,
            seen_claims=persistence.seen_claims,
        )
        # Register only after the bundle is fully built: if the build raises,
        # the except returns (None, None, None) and the engine must not retain
        # an observer that would publish to an unstarted queue on the
        # in-process fallback path.
        engine.register_observer(dispatcher.on_task_state_changed)
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            note="Failed to register distributed dispatcher",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None, None, None

    logger.info(
        API_SERVICE_AUTO_WIRED,
        service="distributed_dispatcher",
    )
    return task_queue, dispatcher, backend_services


def _auto_wire_message_bus(
    effective_config: RootConfig,
) -> MessageBus:
    """Create the configured MessageBus with API channels merged in.

    Dispatches to the correct backend via ``build_message_bus`` based on
    ``communication.message_bus.backend``. The API bridge needs the additional
    channels in ``ALL_CHANNELS`` (see ``synthorg.api.channels``) to forward
    events to WebSocket clients, so they are merged in before the factory runs.

    Args:
        effective_config: Root company configuration.

    Returns:
        A configured ``MessageBus`` instance (not started).
    """
    from synthorg.communication.bus import build_message_bus  # noqa: PLC0415

    try:
        bus_config = effective_config.communication.message_bus
        extra = tuple(ch for ch in ALL_CHANNELS if ch not in bus_config.channels)
        if extra:
            bus_config = bus_config.model_copy(
                update={"channels": (*bus_config.channels, *extra)},
            )
        bus = build_message_bus(bus_config)
    except Exception as exc:
        log_exception_redacted(
            logger, API_APP_STARTUP, exc, note="Failed to auto-wire message bus"
        )
        raise
    logger.info(
        API_SERVICE_AUTO_WIRED,
        service="message_bus",
        backend=bus_config.backend.value,
    )
    return bus
