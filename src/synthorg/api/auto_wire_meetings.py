# module-kind: code
"""Construction-time meeting service auto-wiring.

Creates the meeting orchestrator + scheduler + ceremony scheduler, returning
them in a :class:`MeetingWireResult`.
"""

from collections.abc import Mapping
from typing import NamedTuple

from synthorg.communication.meeting.agent_caller import (
    build_meeting_agent_caller,
    build_unconfigured_meeting_agent_caller,
)
from synthorg.communication.meeting.enums import MeetingProtocolType
from synthorg.communication.meeting.orchestrator import MeetingOrchestrator
from synthorg.communication.meeting.participant import ParticipantResolver
from synthorg.communication.meeting.protocol import AgentCaller, MeetingProtocol
from synthorg.communication.meeting.scheduler import MeetingScheduler
from synthorg.config.schema import RootConfig
from synthorg.engine.strategy.lens_assignment import DiversityMaximizingAssigner
from synthorg.engine.strategy.models import StrategyConfig
from synthorg.engine.workflow.ceremony_scheduler import CeremonyScheduler
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_MEETINGS_WIRING_DEFERRED,
    API_SERVICE_AUTO_WIRED,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)


class MeetingWireResult(NamedTuple):
    """Services created during meeting auto-wiring.

    ``meeting_orchestrator`` is always non-``None``. ``meeting_scheduler`` and
    ``ceremony_scheduler`` are ``None`` when auto-wiring discovered missing
    dependencies (agent_registry / provider_registry) and so the caller is
    known-failing -- running scheduled meetings against a caller that is
    guaranteed to raise would produce background noise with no useful output,
    so the schedulers are intentionally not wired until the operator provides
    the missing dependencies. Explicit values always pass through unchanged.
    """

    meeting_orchestrator: MeetingOrchestrator
    meeting_scheduler: MeetingScheduler | None
    ceremony_scheduler: CeremonyScheduler | None


def auto_wire_meetings(  # noqa: PLR0913 -- meeting wiring needs the full dep set
    *,
    effective_config: RootConfig,
    meeting_orchestrator: MeetingOrchestrator | None,
    meeting_scheduler: MeetingScheduler | None,
    agent_registry: AgentRegistryService | None,
    provider_registry: ProviderRegistry | None,
    persistence: PersistenceBackend | None = None,
) -> MeetingWireResult:
    """Auto-wire meeting orchestrator and scheduler.

    Each service is created only when the caller passes ``None``. Explicit
    values are preserved unchanged. This runs at construction time -- meeting
    services don't need connected persistence.

    When auto-wiring the orchestrator without an agent registry or provider
    registry, the resulting agent caller is guaranteed to raise
    :class:`MeetingAgentCallerNotConfiguredError` at call time. Running
    scheduled meetings against a known-failing caller only produces background
    noise, so ``meeting_scheduler`` and ``ceremony_scheduler`` are
    intentionally left ``None`` in that case.

    Args:
        effective_config: Root company configuration.
        meeting_orchestrator: Explicit orchestrator or ``None`` to auto-wire.
        meeting_scheduler: Explicit scheduler or ``None`` to auto-wire.
        agent_registry: Agent registry. Required when auto-wiring the
            orchestrator so meeting turns can resolve agent identities.
        provider_registry: Provider registry. Used for real LLM dispatch per
            meeting turn when auto-wiring. When ``None``, an unconfigured caller
            is wired that raises at first invocation.
        persistence: Optional connected persistence backend. When supplied, the
            ceremony scheduler is wired with the
            ``CeremonySchedulerStateRepository`` so its per-sprint state
            survives process restarts.

    Returns:
        A ``MeetingWireResult``. ``meeting_scheduler`` and
        ``ceremony_scheduler`` may be ``None`` when the auto-wired orchestrator
        has a known-failing caller (see docstring).
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
            strategy_config=effective_config.strategy,
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

    # Skip scheduler/ceremony wiring only when the auto-wired orchestrator has
    # a guaranteed-failing caller AND the operator did not supply an explicit
    # scheduler. Explicit schedulers always pass through unchanged.
    skip_scheduler_wiring = (
        orchestrator_was_auto_wired
        and bool(missing_dependencies)
        and meeting_scheduler is None
    )

    if orchestrator_was_auto_wired and missing_dependencies:
        # One consolidated record for an auto-wired orchestrator left with
        # an unconfigured caller. ``provider_registry is None`` is the
        # expected empty-company / pre-setup state (this runs at
        # construction time, before the provider registry is wired), so it
        # logs at INFO; any other missing dependency is unexpected and logs
        # at WARNING. Replaces the former per-site warnings here and in
        # ``_wire_meeting_orchestrator`` that emitted two records per boot.
        log = logger.info if provider_registry is None else logger.warning
        log(
            API_MEETINGS_WIRING_DEFERRED,
            missing_dependencies=missing_dependencies,
            schedulers_deferred=skip_scheduler_wiring,
            note=(
                "Meeting stack wired with an unconfigured agent caller; "
                "agent invocation and scheduled meetings stay deferred "
                "until the missing dependencies are provided"
            ),
        )

    if skip_scheduler_wiring:
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
            persistence=persistence,
        )

    try:
        ceremony_scheduler = CeremonyScheduler(
            meeting_scheduler=meeting_scheduler,
            state_repo=(
                persistence.ceremony_scheduler_state
                if persistence is not None and persistence.is_connected
                else None
            ),
        )
    except Exception as exc:
        log_exception_redacted(
            logger, API_APP_STARTUP, exc, note="Failed to auto-wire ceremony scheduler"
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

    Uses default per-protocol configs from the Pydantic models. The protocol
    type selected per meeting is determined by ``MeetingProtocolConfig.protocol``,
    not by the registry.

    Returns:
        Mapping from protocol type to implementation.

    Raises:
        RuntimeError: When the registry size doesn't match the protocol enum.
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
        logger.error(
            API_APP_STARTUP,
            action="meeting_protocol_registry_incomplete",
            registry_size=len(registry),
            expected_size=len(MeetingProtocolType),
            error_type=RuntimeError.__name__,
        )
        raise RuntimeError(msg)

    return registry


def _wire_meeting_orchestrator(
    *,
    agent_registry: AgentRegistryService | None,
    provider_registry: ProviderRegistry | None,
    strategy_config: StrategyConfig,
) -> MeetingOrchestrator:
    """Create a MeetingOrchestrator wired to real LLM dispatch.

    When both *agent_registry* and *provider_registry* are available, the
    orchestrator dispatches real LLM calls per turn. When either is missing,
    the orchestrator is still constructed so the REST surface stays available,
    but any attempt to invoke an agent raises
    :class:`MeetingAgentCallerNotConfiguredError` at call time.

    The diversity-maximising lens assigner is wired with the strategy
    config's default lenses so each meeting distributes distinct strategic
    viewpoints across participants.

    Args:
        agent_registry: Source of truth for agent identity lookup, or ``None``.
        provider_registry: Source of truth for LLM providers, or ``None``.
        strategy_config: Strategy configuration supplying the default lenses
            for participant lens assignment.

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
            # Built silently: the single consolidated
            # ``API_MEETINGS_WIRING_DEFERRED`` record in ``auto_wire_meetings``
            # owns operator messaging for the unconfigured-caller state, so a
            # second warning here would just duplicate it per boot.
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
            strategy_config=strategy_config,
            lens_assigner=DiversityMaximizingAssigner(),
        )
    except Exception as exc:
        log_exception_redacted(
            logger,
            API_APP_STARTUP,
            exc,
            note="Failed to auto-wire meeting orchestrator",
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
        ``RegistryParticipantResolver`` if *agent_registry* is available,
        otherwise ``PassthroughParticipantResolver``.
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
            "No agent registry available -- meeting scheduler using passthrough "
            "participant resolver (literal IDs only)"
        ),
    )
    return PassthroughParticipantResolver()


def _wire_meeting_scheduler(
    effective_config: RootConfig,
    orchestrator: MeetingOrchestrator,
    agent_registry: AgentRegistryService | None,
    persistence: PersistenceBackend | None = None,
) -> MeetingScheduler:
    """Create a MeetingScheduler with participant resolver.

    Args:
        effective_config: Root company configuration.
        orchestrator: Meeting orchestrator instance.
        agent_registry: Agent registry (may be ``None``).
        persistence: Optional connected persistence backend. When supplied, the
            scheduler is wired with the ``MeetingCooldownRepository`` so its
            per-meeting-type cooldown timestamps survive process restarts.

    Returns:
        A configured ``MeetingScheduler`` instance.
    """
    try:
        resolver = _select_participant_resolver(agent_registry)
        scheduler = MeetingScheduler(
            config=effective_config.communication.meetings,
            orchestrator=orchestrator,
            participant_resolver=resolver,
            cooldown_repo=(
                persistence.meeting_cooldown
                if persistence is not None and persistence.is_connected
                else None
            ),
        )
    except Exception as exc:
        log_exception_redacted(
            logger, API_APP_STARTUP, exc, note="Failed to auto-wire meeting scheduler"
        )
        raise
    logger.info(API_SERVICE_AUTO_WIRED, service="meeting_scheduler")
    return scheduler
