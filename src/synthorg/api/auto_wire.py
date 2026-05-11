"""Service auto-wiring for production startup.

Construction time: creates services that don't need a connected
persistence backend -- message bus, cost tracker, provider registry,
task engine, provider health tracker.

Meeting auto-wire (construction time): creates meeting orchestrator
and meeting scheduler (same lifecycle as the construction-time wire
but a separate call).

On-startup: creates SettingsService + dispatcher after persistence
connects and migrations complete.
"""

import contextlib
from collections.abc import Callable
from typing import TYPE_CHECKING, NamedTuple

from synthorg.api.channels import ALL_CHANNELS
from synthorg.budget.tracker import CostTracker
from synthorg.communication.meeting.agent_caller import (
    build_meeting_agent_caller,
    build_unconfigured_meeting_agent_caller,
)
from synthorg.communication.meeting.enums import MeetingProtocolType
from synthorg.communication.meeting.orchestrator import MeetingOrchestrator
from synthorg.communication.meeting.scheduler import MeetingScheduler
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.workflow.ceremony_scheduler import CeremonyScheduler
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_SERVICE_AUTO_WIRED,
)
from synthorg.providers.health import ProviderHealthTracker
from synthorg.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.api.state import AppState
    from synthorg.backup.service import BackupService
    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.communication.config import NatsConfig
    from synthorg.communication.meeting.participant import (
        ParticipantResolver,
    )
    from synthorg.communication.meeting.protocol import (
        AgentCaller,
        MeetingProtocol,
    )
    from synthorg.config.schema import RootConfig
    from synthorg.hr.registry import AgentRegistryService
    from synthorg.ontology.service import OntologyService
    from synthorg.persistence.protocol import PersistenceBackend
    from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
    from synthorg.settings.dispatcher import SettingsChangeDispatcher
    from synthorg.settings.service import SettingsService
    from synthorg.workers.claim import JetStreamTaskQueue
    from synthorg.workers.config import QueueConfig

logger = get_logger(__name__)

type SettingsDispatcherBuilder = Callable[
    [
        MessageBus | None,
        SettingsService | None,
        RootConfig,
        AppState,
        BackupService | None,
        ApprovalTimeoutScheduler | None,
    ],
    SettingsChangeDispatcher | None,
]
"""Callable that builds the SettingsChangeDispatcher during on-startup wiring."""


class Phase1Result(NamedTuple):
    """Services created during construction-time auto-wiring."""

    message_bus: MessageBus | None
    cost_tracker: CostTracker | None
    task_engine: TaskEngine | None
    provider_registry: ProviderRegistry | None
    provider_health_tracker: ProviderHealthTracker | None
    distributed_task_queue: JetStreamTaskQueue | None


class MeetingWireResult(NamedTuple):
    """Services created during meeting auto-wiring.

    ``meeting_orchestrator`` is always non-``None``.  ``meeting_scheduler``
    and ``ceremony_scheduler`` are ``None`` when auto-wiring discovered
    missing dependencies (agent_registry / provider_registry) and so the
    caller is known-failing -- running scheduled meetings against a
    caller that is guaranteed to raise would produce background noise
    with no useful output, so the schedulers are intentionally not
    wired until the operator provides the missing dependencies.
    Explicit values always pass through unchanged.
    """

    meeting_orchestrator: MeetingOrchestrator
    meeting_scheduler: MeetingScheduler | None
    ceremony_scheduler: CeremonyScheduler | None


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

    Each service is created only when the caller passes ``None``.
    Explicit values are preserved unchanged.

    Args:
        effective_config: Root company configuration.
        persistence: Persistence backend (may be ``None``).  When
            ``None``, ``task_engine`` cannot be auto-wired and a
            warning is logged.
        message_bus: Explicit bus or ``None`` to auto-wire.
        cost_tracker: Explicit tracker or ``None`` to auto-wire.
        task_engine: Explicit engine or ``None`` to auto-wire.
        provider_registry: Explicit registry or ``None`` to auto-wire.
        provider_health_tracker: Explicit tracker or ``None`` to
            auto-wire.

    Returns:
        A ``Phase1Result`` with all (possibly auto-wired) services.
    """
    distributed_task_queue: JetStreamTaskQueue | None = None

    if message_bus is None:
        message_bus = _auto_wire_message_bus(effective_config)

    if cost_tracker is None:
        cost_tracker = _wire_cost_tracker(effective_config)

    if provider_registry is None and effective_config.providers:
        provider_registry = _wire_provider_registry(effective_config)

    if task_engine is None and persistence is not None:
        task_engine, distributed_task_queue = _wire_task_engine(
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
    )


def _wire_cost_tracker(effective_config: RootConfig) -> CostTracker:
    """Create a CostTracker from config."""
    try:
        tracker = CostTracker(budget_config=effective_config.budget)
    except Exception as exc:
        logger.error(
            API_APP_STARTUP,
            note="Failed to auto-wire cost tracker",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise
    logger.info(API_SERVICE_AUTO_WIRED, service="cost_tracker")
    return tracker


def _wire_provider_registry(
    effective_config: RootConfig,
) -> ProviderRegistry:
    """Create a ProviderRegistry from config."""
    try:
        registry = ProviderRegistry.from_config(effective_config.providers)
    except Exception as exc:
        logger.error(
            API_APP_STARTUP,
            note="Failed to build provider registry from config",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise
    logger.info(API_SERVICE_AUTO_WIRED, service="provider_registry")
    return registry


def _wire_task_engine(
    persistence: PersistenceBackend,
    message_bus: MessageBus | None,
    queue_config: QueueConfig | None = None,
    nats_config: NatsConfig | None = None,
) -> tuple[TaskEngine, JetStreamTaskQueue | None]:
    """Create a TaskEngine from persistence and optional bus.

    When ``queue_config.enabled`` is true, also create a
    :class:`JetStreamTaskQueue` and register a
    :class:`DistributedDispatcher` observer so task state changes are
    published to the distributed work queue. The caller owns the
    returned task queue's async lifecycle: it must be ``start()``ed
    before any observer events fire and ``stop()``ped during shutdown
    to avoid leaked NATS connections. The dispatcher registration
    itself is synchronous.

    Returns:
        A ``(task_engine, task_queue)`` tuple. ``task_queue`` is
        non-``None`` only when ``queue_config.enabled`` is true, the
        ``nats_config`` is present, and ``synthorg[distributed]`` is
        installed; otherwise it is ``None`` and the in-process path is
        used.
    """
    try:
        engine = TaskEngine(
            persistence=persistence,
            message_bus=message_bus,
        )
    except Exception as exc:
        logger.error(
            API_APP_STARTUP,
            note="Failed to auto-wire task engine",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise

    task_queue: JetStreamTaskQueue | None = None
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
            task_queue = _register_distributed_dispatcher(
                engine,
                queue_config,
                nats_config,
            )

    logger.info(API_SERVICE_AUTO_WIRED, service="task_engine")
    return engine, task_queue


def _register_distributed_dispatcher(
    engine: TaskEngine,
    queue_config: QueueConfig,
    nats_config: NatsConfig,
) -> JetStreamTaskQueue | None:
    """Register the distributed dispatcher observer on the task engine.

    Creates a :class:`JetStreamTaskQueue` (not started) and a
    :class:`DistributedDispatcher` observer. Registration is
    idempotent and best-effort: any failure here is logged but does
    not abort startup, because the in-process path remains viable.

    Returns the constructed queue so the caller can drive its async
    ``start()``/``stop()`` lifecycle; returns ``None`` when the
    optional ``synthorg[distributed]`` dependency is missing or
    construction itself fails.
    """
    try:
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
        return None

    try:
        task_queue = JetStreamTaskQueue(
            queue_config=queue_config,
            nats_config=nats_config,
        )
        dispatcher = DistributedDispatcher(task_queue=task_queue)
        engine.register_observer(dispatcher.on_task_state_changed)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            note="Failed to register distributed dispatcher",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None

    logger.info(
        API_SERVICE_AUTO_WIRED,
        service="distributed_dispatcher",
    )
    return task_queue


def _auto_wire_message_bus(
    effective_config: RootConfig,
) -> MessageBus:
    """Create the configured MessageBus with API channels merged in.

    Dispatches to the correct backend via ``build_message_bus`` based
    on ``communication.message_bus.backend``. The default
    ``MessageBusConfig`` channels are organizational (``#all-hands``,
    ``#engineering``, etc.). The API bridge needs additional channels
    defined in ``ALL_CHANNELS`` (see ``synthorg.api.channels``) to
    forward events to WebSocket clients, so they are merged in here
    before the factory runs.

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
        logger.error(
            API_APP_STARTUP,
            note="Failed to auto-wire message bus",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise
    logger.info(
        API_SERVICE_AUTO_WIRED,
        service="message_bus",
        backend=bus_config.backend.value,
    )
    return bus


def auto_wire_meetings(
    *,
    effective_config: RootConfig,
    meeting_orchestrator: MeetingOrchestrator | None,
    meeting_scheduler: MeetingScheduler | None,
    agent_registry: AgentRegistryService | None,
    provider_registry: ProviderRegistry | None,
) -> MeetingWireResult:
    """Auto-wire meeting orchestrator and scheduler.

    Each service is created only when the caller passes ``None``.
    Explicit values are preserved unchanged.  This runs at construction
    time -- meeting services don't need connected persistence.

    When auto-wiring the orchestrator without an agent registry or
    provider registry, the resulting agent caller is guaranteed to
    raise :class:`MeetingAgentCallerNotConfiguredError` at call time.
    Running scheduled meetings against a known-failing caller only
    produces background noise, so ``meeting_scheduler`` and
    ``ceremony_scheduler`` are intentionally left ``None`` in that
    case.  Providing the missing dependencies on a subsequent app
    build wires the schedulers for real.

    Args:
        effective_config: Root company configuration.
        meeting_orchestrator: Explicit orchestrator or ``None`` to
            auto-wire.
        meeting_scheduler: Explicit scheduler or ``None`` to auto-wire.
        agent_registry: Agent registry.  Required when auto-wiring the
            orchestrator so meeting turns can resolve agent identities.
            May be ``None`` when *meeting_orchestrator* is supplied
            explicitly; in that case a passthrough participant resolver
            is used for scheduling.
        provider_registry: Provider registry.  Used for real LLM
            dispatch per meeting turn when auto-wiring.  When ``None``,
            an unconfigured caller is wired that raises
            :class:`MeetingAgentCallerNotConfiguredError` at first
            invocation -- the REST surface stays available but agent
            calls fail loudly with actionable error context.

    Returns:
        A ``MeetingWireResult``.  ``meeting_scheduler`` and
        ``ceremony_scheduler`` may be ``None`` when the auto-wired
        orchestrator has a known-failing caller (see docstring).
    """
    orchestrator_was_auto_wired = meeting_orchestrator is None
    missing_dependencies: tuple[str, ...] = _missing_meeting_dependencies(
        agent_registry=agent_registry,
        provider_registry=provider_registry,
    )

    if meeting_orchestrator is None:
        meeting_orchestrator = _wire_meeting_orchestrator(
            agent_registry=agent_registry,
            provider_registry=provider_registry,
        )
        if meeting_scheduler is not None:
            logger.warning(
                API_APP_STARTUP,
                note=(
                    "Auto-wired a new orchestrator but using an explicit "
                    "scheduler -- the scheduler's internal orchestrator "
                    "reference will diverge from the auto-wired one. "
                    "Provide both or neither for consistent state"
                ),
            )

    # Skip scheduler/ceremony wiring only when the auto-wired
    # orchestrator has a guaranteed-failing caller AND the operator
    # did not supply an explicit scheduler.  Explicit schedulers always
    # pass through unchanged so operators can mix wiring strategies.
    skip_scheduler_wiring = (
        orchestrator_was_auto_wired
        and bool(missing_dependencies)
        and meeting_scheduler is None
    )

    if skip_scheduler_wiring:
        logger.warning(
            API_APP_STARTUP,
            note=(
                "Skipping MeetingScheduler and CeremonyScheduler wiring "
                "because meeting agent caller is unconfigured -- "
                "scheduled meetings would invoke a caller guaranteed "
                "to raise MeetingAgentCallerNotConfiguredError.  Provide "
                "the missing dependencies to wire the full meeting stack"
            ),
            missing_dependencies=missing_dependencies,
        )
        return MeetingWireResult(
            meeting_orchestrator=meeting_orchestrator,
            meeting_scheduler=None,
            ceremony_scheduler=None,
        )

    if meeting_scheduler is None:
        meeting_scheduler = _wire_meeting_scheduler(
            effective_config,
            meeting_orchestrator,
            agent_registry,
        )

    try:
        ceremony_scheduler = CeremonyScheduler(
            meeting_scheduler=meeting_scheduler,
        )
    except Exception as exc:
        logger.error(
            API_APP_STARTUP,
            note="Failed to auto-wire ceremony scheduler",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise
    logger.info(API_SERVICE_AUTO_WIRED, service="ceremony_scheduler")

    return MeetingWireResult(
        meeting_orchestrator=meeting_orchestrator,
        meeting_scheduler=meeting_scheduler,
        ceremony_scheduler=ceremony_scheduler,
    )


def _missing_meeting_dependencies(
    *,
    agent_registry: AgentRegistryService | None,
    provider_registry: ProviderRegistry | None,
) -> tuple[str, ...]:
    """Return the names of meeting dependencies that are ``None``."""
    missing: list[str] = []
    if agent_registry is None:
        missing.append("agent_registry")
    if provider_registry is None:
        missing.append("provider_registry")
    return tuple(missing)


def _build_protocol_registry() -> Mapping[MeetingProtocolType, MeetingProtocol]:
    """Create a registry of all meeting protocol implementations.

    Uses default per-protocol configs from the Pydantic models.
    The protocol type selected per meeting is determined by
    ``MeetingProtocolConfig.protocol``, not by the registry.

    Returns:
        Mapping from protocol type to implementation.
    """
    # Deferred imports to avoid heavy transitive deps at module level.
    from synthorg.communication.meeting.config import (  # noqa: PLC0415
        PositionPapersConfig,
        RoundRobinConfig,
        StructuredPhasesConfig,
    )
    from synthorg.communication.meeting.position_papers import (  # noqa: PLC0415
        PositionPapersProtocol,
    )
    from synthorg.communication.meeting.round_robin import (  # noqa: PLC0415
        RoundRobinProtocol,
    )
    from synthorg.communication.meeting.structured_phases import (  # noqa: PLC0415
        StructuredPhasesProtocol,
    )

    registry: dict[MeetingProtocolType, MeetingProtocol] = {
        MeetingProtocolType.ROUND_ROBIN: RoundRobinProtocol(
            RoundRobinConfig(),
        ),
        MeetingProtocolType.POSITION_PAPERS: PositionPapersProtocol(
            PositionPapersConfig(),
        ),
        MeetingProtocolType.STRUCTURED_PHASES: StructuredPhasesProtocol(
            StructuredPhasesConfig(),
        ),
    }

    if len(registry) != len(MeetingProtocolType):
        msg = (
            f"Protocol registry has {len(registry)} entries but "
            f"{len(MeetingProtocolType)} protocol types exist"
        )
        raise RuntimeError(msg)

    return registry


def _wire_meeting_orchestrator(
    *,
    agent_registry: AgentRegistryService | None,
    provider_registry: ProviderRegistry | None,
) -> MeetingOrchestrator:
    """Create a MeetingOrchestrator wired to real LLM dispatch.

    When both *agent_registry* and *provider_registry* are available,
    the orchestrator dispatches real LLM calls per turn.  When either
    is missing, the orchestrator is still constructed so the REST
    surface stays available, but any attempt to invoke an agent raises
    :class:`MeetingAgentCallerNotConfiguredError` at call time -- no
    silent empty responses.

    Args:
        agent_registry: Source of truth for agent identity lookup,
            or ``None`` when not yet available.
        provider_registry: Source of truth for LLM providers,
            or ``None`` when not yet available.

    Returns:
        A configured ``MeetingOrchestrator``.
    """
    try:
        protocol_registry = _build_protocol_registry()
        missing = _missing_meeting_dependencies(
            agent_registry=agent_registry,
            provider_registry=provider_registry,
        )
        if missing:
            logger.warning(
                API_APP_STARTUP,
                note=(
                    "MeetingOrchestrator wired with an unconfigured agent "
                    "caller; agent invocation will fail at call time until "
                    "the missing dependencies are provided"
                ),
                missing_dependencies=missing,
            )
            agent_caller: AgentCaller = build_unconfigured_meeting_agent_caller(
                missing_dependencies=missing,
            )
        else:
            # Both registries are non-None (the `missing` check above).
            assert agent_registry is not None  # noqa: S101
            assert provider_registry is not None  # noqa: S101
            agent_caller = build_meeting_agent_caller(
                agent_registry=agent_registry,
                provider_registry=provider_registry,
            )
        orchestrator = MeetingOrchestrator(
            protocol_registry=protocol_registry,
            agent_caller=agent_caller,
        )
    except Exception as exc:
        logger.error(
            API_APP_STARTUP,
            note="Failed to auto-wire meeting orchestrator",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise
    logger.info(API_SERVICE_AUTO_WIRED, service="meeting_orchestrator")
    return orchestrator


def _select_participant_resolver(
    agent_registry: AgentRegistryService | None,
) -> ParticipantResolver:
    """Choose a participant resolver based on registry availability.

    Args:
        agent_registry: Agent registry (may be ``None``).

    Returns:
        ``RegistryParticipantResolver`` if *agent_registry* is
        available, otherwise ``PassthroughParticipantResolver``.
    """
    from synthorg.communication.meeting.participant import (  # noqa: PLC0415
        PassthroughParticipantResolver,
        RegistryParticipantResolver,
    )

    if agent_registry is not None:
        return RegistryParticipantResolver(agent_registry)
    logger.warning(
        API_APP_STARTUP,
        note=(
            "No agent registry available -- meeting "
            "scheduler using passthrough participant "
            "resolver (literal IDs only)"
        ),
    )
    return PassthroughParticipantResolver()


def _wire_meeting_scheduler(
    effective_config: RootConfig,
    orchestrator: MeetingOrchestrator,
    agent_registry: AgentRegistryService | None,
) -> MeetingScheduler:
    """Create a MeetingScheduler with participant resolver.

    Args:
        effective_config: Root company configuration.
        orchestrator: Meeting orchestrator instance.
        agent_registry: Agent registry (may be ``None``).

    Returns:
        A configured ``MeetingScheduler`` instance.
    """
    try:
        resolver = _select_participant_resolver(agent_registry)
        scheduler = MeetingScheduler(
            config=effective_config.communication.meetings,
            orchestrator=orchestrator,
            participant_resolver=resolver,
        )
    except Exception as exc:
        logger.error(
            API_APP_STARTUP,
            note="Failed to auto-wire meeting scheduler",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise
    logger.info(API_SERVICE_AUTO_WIRED, service="meeting_scheduler")
    return scheduler


async def auto_wire_settings(  # noqa: PLR0913
    persistence: PersistenceBackend,
    message_bus: MessageBus | None,
    effective_config: RootConfig,
    app_state: AppState,
    backup_service: BackupService | None,
    build_dispatcher: SettingsDispatcherBuilder,
    approval_timeout_scheduler: ApprovalTimeoutScheduler | None = None,
) -> SettingsChangeDispatcher | None:
    """On-startup auto-wire: create SettingsService after persistence connects.

    Called from ``on_startup`` after persistence connects.  Creates
    the settings service, starts the dispatcher, and only then injects
    the service into *app_state* (to avoid partial state corruption if
    the dispatcher fails to start).

    Args:
        persistence: Connected persistence backend.
        message_bus: Message bus instance (may be ``None``).
        effective_config: Root company configuration.
        app_state: Application state container.
        backup_service: Backup service (for settings subscriber wiring).
        build_dispatcher: Callable that builds a settings dispatcher.
        approval_timeout_scheduler: Approval-timeout scheduler so the
            dispatcher can wire the matching subscriber for
            ``security.timeout_check_interval_seconds``.

    Returns:
        The started dispatcher, or ``None`` if ``build_dispatcher``
        returns ``None`` (typically when no message bus is available).
    """
    # Deferred to break import cycle: settings.* -> api.* -> auto_wire
    import synthorg.settings.definitions  # noqa: F401, PLC0415
    from synthorg.settings.encryption import SettingsEncryptor  # noqa: PLC0415
    from synthorg.settings.registry import get_registry  # noqa: PLC0415
    from synthorg.settings.service import SettingsService  # noqa: PLC0415

    try:
        encryptor = SettingsEncryptor.from_env()
        settings_svc = SettingsService(
            repository=persistence.settings,
            registry=get_registry(),
            config=effective_config,
            encryptor=encryptor,
            message_bus=message_bus,
        )
    except Exception as exc:
        logger.error(
            API_APP_STARTUP,
            note=(
                "Failed to create SettingsService -- check encryption key configuration"
            ),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise

    # Build and start the dispatcher BEFORE mutating AppState, so a
    # dispatcher.start() failure doesn't leave app_state with a
    # settings service that has no running dispatcher.
    try:
        dispatcher = build_dispatcher(
            message_bus,
            settings_svc,
            effective_config,
            app_state,
            backup_service,
            approval_timeout_scheduler,
        )
    except Exception as exc:
        logger.error(
            API_APP_STARTUP,
            note="Failed to build settings dispatcher",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise

    if dispatcher is not None:
        try:
            await dispatcher.start()
        except Exception as exc:
            logger.error(
                API_APP_STARTUP,
                note="Failed to start auto-wired settings dispatcher",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.info(API_SERVICE_AUTO_WIRED, service="settings_dispatcher")

    # All fallible operations succeeded -- safe to mutate AppState.
    # If set_settings_service fails, stop the dispatcher to prevent leaks.
    try:
        app_state.set_settings_service(settings_svc)
    except Exception:
        if dispatcher is not None:
            with contextlib.suppress(Exception):
                await dispatcher.stop()
        raise
    logger.info(API_SERVICE_AUTO_WIRED, service="settings_service")
    return dispatcher


async def auto_wire_ontology(
    effective_config: RootConfig,
    persistence: PersistenceBackend | None = None,
) -> OntologyService | None:
    """Auto-wire the ontology subsystem on top of the shared persistence backend.

    Wires versioning on top of ``persistence.get_db()``, constructs
    the ``OntologyService`` against ``persistence.ontology_entities``,
    and runs bootstrap (decorator registry + config entities).

    Args:
        effective_config: Root company configuration.
        persistence: Connected persistence backend (required).  Safe
            to pass ``None`` -- the wire-up simply no-ops when the
            backend is absent, matching prior behaviour for
            ontology-disabled deployments.

    Returns:
        The bootstrapped ``OntologyService``, or ``None`` if wiring
        fails (non-fatal -- ontology is not required for startup).
    """
    from synthorg.observability.events.ontology import (  # noqa: PLC0415
        ONTOLOGY_AUTO_WIRE_FAILED,
    )
    from synthorg.ontology.service import OntologyService  # noqa: PLC0415

    ontology_config = effective_config.ontology
    if persistence is None or not getattr(persistence, "is_connected", False):
        logger.warning(
            ONTOLOGY_AUTO_WIRE_FAILED,
            error_type="NoPersistence",
            error="ontology auto-wire requires a connected persistence backend",
        )
        return None

    # Ontology runs on the shared persistence backend: the
    # entity repository and versioning service both take the backend's
    # active DB handle, so schema migrations (Atlas) and connection
    # lifecycle are owned by PersistenceBackend.  The backend's
    # ``build_ontology_versioning`` factory selects the correct
    # versioning service (SQLite aiosqlite vs Postgres pool) without
    # leaking concrete-backend identity to this call site.
    backend = persistence.ontology_entities
    try:
        versioning = persistence.build_ontology_versioning()
        service = OntologyService(
            backend=backend,
            versioning=versioning,
            config=ontology_config,
        )
        await service.bootstrap()
        if ontology_config.entities.entries:
            await service.bootstrap_from_config(ontology_config.entities)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            ONTOLOGY_AUTO_WIRE_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    else:
        logger.info(API_SERVICE_AUTO_WIRED, service="ontology_service")
        return service
