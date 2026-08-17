# module-kind: code
"""Construction-time meeting orchestrator auto-wiring.

Creates the meeting orchestrator, returning it in a
:class:`MeetingWireResult`.

Neither the orchestrator's protocol registry nor its agent caller is built
here, and neither is the scheduler stack the orchestrator drives. All three
need collaborators that do not exist yet at construction: the factories bake
in organisation-wide strategy policy read from settings, and the caller is
composed from the provider registry, which is wired once persistence is up.
The ``meeting_protocol_registry``, ``meeting_agent_dispatch`` and
``ceremony_scheduler`` subsystems own them, so the reconciler installs each
on the pass where its dependencies are present rather than skipping it for
the life of the process.

What is built here is the object every other surface holds a reference to,
which is why it is built unconditionally: the meetings REST surface, the
conflict-escalation bridge and the strategy context all bind it during
construction, so replacing it later would strand them.
"""

from typing import NamedTuple

from synthorg.communication.meeting.agent_caller import (
    build_unconfigured_meeting_agent_caller,
)
from synthorg.communication.meeting.orchestrator import MeetingOrchestrator
from synthorg.communication.meeting.protocol import AgentCaller
from synthorg.communication.meeting.scheduler import MeetingScheduler
from synthorg.config.schema import RootConfig
from synthorg.engine.strategy.lens_assignment import DiversityMaximizingAssigner
from synthorg.engine.strategy.models import StrategyConfig
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_MEETINGS_WIRING_DEFERRED,
    API_SERVICE_AUTO_WIRED,
)
from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)


class MeetingWireResult(NamedTuple):
    """Services created during meeting auto-wiring.

    ``meeting_orchestrator`` is always non-``None``. ``meeting_scheduler``
    is whatever the caller supplied: construction never builds one, because
    a scheduler built here would run meetings through a caller that cannot
    dispatch. The ``ceremony_scheduler`` subsystem builds both once the
    provider registry exists.
    """

    meeting_orchestrator: MeetingOrchestrator
    meeting_scheduler: MeetingScheduler | None


def auto_wire_meetings(
    *,
    effective_config: RootConfig,
    meeting_orchestrator: MeetingOrchestrator | None,
    meeting_scheduler: MeetingScheduler | None,
    agent_registry: AgentRegistryService | None,
    provider_registry: ProviderRegistry | None,
) -> MeetingWireResult:
    """Auto-wire the meeting orchestrator.

    The orchestrator is created only when the caller passes ``None``;
    an explicit value is preserved unchanged, as is an explicit scheduler.

    Args:
        effective_config: Root company configuration.
        meeting_orchestrator: Explicit orchestrator or ``None`` to auto-wire.
        meeting_scheduler: Explicit scheduler, passed through unchanged.
        agent_registry: Agent registry, used to resolve agent identities per
            meeting turn. ``None`` installs a refusing caller, exactly as a
            missing provider registry does; the ``meeting_agent_dispatch``
            subsystem replaces it once the registry exists.
        provider_registry: Provider registry, used for real LLM dispatch per
            meeting turn. ``None`` at construction on every normal boot, so
            a refusing caller is installed and the ``meeting_agent_dispatch``
            subsystem replaces it once the registry exists.

    Returns:
        A ``MeetingWireResult``.
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

    if orchestrator_was_auto_wired and missing_dependencies:
        # One record per auto-wired orchestrator left with a refusing
        # caller, so an operator reads the whole deferral once rather than
        # a fragment per absent dependency. A missing ``provider_registry``
        # ALONE is the expected state here, since this runs at construction
        # and the registry is wired later, so it is INFO; any other shape
        # (``agent_registry`` absent, or both) is a wiring fault and is
        # WARNING.
        expected_pre_setup = missing_dependencies == ("provider_registry",)
        log = logger.info if expected_pre_setup else logger.warning
        log(
            API_MEETINGS_WIRING_DEFERRED,
            missing_dependencies=missing_dependencies,
            note=(
                "Meeting orchestrator built with a refusing agent caller; "
                "the meeting_agent_dispatch subsystem installs real dispatch "
                "on the pass where the missing dependencies are present"
            ),
        )

    return MeetingWireResult(
        meeting_orchestrator=meeting_orchestrator,
        meeting_scheduler=meeting_scheduler,
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


def _wire_meeting_orchestrator(
    *,
    agent_registry: AgentRegistryService | None,
    provider_registry: ProviderRegistry | None,
    strategy_config: StrategyConfig,
) -> MeetingOrchestrator:
    """Create a MeetingOrchestrator with meeting reads available.

    The orchestrator is constructed so the REST surface stays available, but
    any attempt to invoke an agent raises
    :class:`MeetingAgentCallerNotConfiguredError` until the
    ``meeting_agent_dispatch`` subsystem installs the real caller, which it
    does on the pass where both registries are present.

    The orchestrator is built with no protocol registry: the factories bake
    in organisation-wide strategy policy read from settings, which do not
    exist yet at construction time, so the ``meeting_protocol_registry``
    subsystem owns building and installing them and reinstalls a replacement
    when that policy changes.

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
        missing = _missing_meeting_dependencies(
            agent_registry=agent_registry,
            provider_registry=provider_registry,
        )
        # Always refusing, even when both registries happen to be present:
        # dispatch has exactly one owner, and it is the subsystem, which
        # runs on the first reconcile pass. Composing a real caller here as
        # well would leave two answers to "what does a meeting turn dispatch
        # through", differing by whichever wiring path a boot took.
        agent_caller: AgentCaller = build_unconfigured_meeting_agent_caller(
            missing_dependencies=missing or ("meeting_agent_dispatch",),
        )
        orchestrator = MeetingOrchestrator(
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
