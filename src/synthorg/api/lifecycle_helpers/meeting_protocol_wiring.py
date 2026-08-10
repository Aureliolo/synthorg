# module-kind: orchestrator
"""Wiring for the meeting protocol registry.

``wire_meeting_protocol_registry`` is the ``meeting_protocol_registry``
subsystem's ``activate``. It resolves the organisation's consensus-velocity
and premortem policy from settings, binds them behind the meeting package's
hook signatures, and installs the resulting factories on the orchestrator.

The hooks are deliberately built once per activation rather than per meeting:
they derive from organisation-wide policy, not from any one meeting. What
makes an operator's edit reach a meeting is the reconciler rebuilding this
subsystem, which is why the spec declares those settings with
``rebuild_on_change``.
"""

import asyncio
from collections.abc import Mapping

from synthorg._core.features import require_service
from synthorg.api.state import AppState
from synthorg.communication.meeting.enums import MeetingProtocolType
from synthorg.communication.meeting.orchestrator import MeetingOrchestrator
from synthorg.communication.meeting.protocol import MeetingProtocolFactory
from synthorg.communication.state import CommunicationStateSlice
from synthorg.engine.strategy.models import (
    ConsensusAction,
    ConsensusVelocityConfig,
    PremortemConfig,
    PremortemParticipation,
)
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_SERVICE_AUTO_WIRED,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


def _orchestrator(app_state: AppState) -> MeetingOrchestrator:
    """Return the orchestrator the registry is installed on.

    The subsystem declares ``MEETING_ORCHESTRATOR`` in ``requires``, so the
    reconciler has already checked it is there; raising is the honest
    response if that stops being true, rather than declining on a condition
    the declaration says cannot happen.

    Args:
        app_state: Application state carrying the communication slice.

    Returns:
        The wired orchestrator.
    """
    return require_service(
        app_state.slice(CommunicationStateSlice).meeting_orchestrator,
        "Meeting Orchestrator",
    )


async def _resolve_consensus_policy(
    resolver: ConfigResolverProtocol,
) -> ConsensusVelocityConfig:
    """Read the organisation's consensus-velocity policy from settings.

    Args:
        resolver: The live settings resolver (DB > env > default).

    Returns:
        The policy the consensus hook is built from.
    """
    namespace = SettingNamespace.STRATEGY
    return ConsensusVelocityConfig(
        action=await resolver.get_enum(
            namespace,
            "consensus_velocity_action",
            ConsensusAction,
        ),
        threshold=await resolver.get_float(
            namespace,
            "consensus_velocity_threshold",
        ),
    )


async def _resolve_premortem_policy(
    resolver: ConfigResolverProtocol,
) -> PremortemConfig:
    """Read the organisation's premortem policy from settings.

    Args:
        resolver: The live settings resolver (DB > env > default).

    Returns:
        The policy the premortem hook is built from.
    """
    return PremortemConfig(
        participants=await resolver.get_enum(
            SettingNamespace.STRATEGY,
            "premortem_participants",
            PremortemParticipation,
        ),
    )


async def build_protocol_registry(
    resolver: ConfigResolverProtocol,
) -> Mapping[MeetingProtocolType, MeetingProtocolFactory]:
    """Build one protocol factory per type, from live strategy policy.

    A factory builds its protocol from the meeting's own
    ``MeetingProtocolConfig``, so a sub-config an operator set on a meeting
    type or a sprint ceremony reaches the instance that acts on it. The
    premortem and consensus-velocity hooks come from settings instead,
    because they are organisation-wide.

    Args:
        resolver: The live settings resolver.

    Returns:
        Mapping from protocol type to factory.
    """
    # Deferred imports to keep the boot graph out of any cold import of
    # this module.
    from synthorg.api._meeting_strategy_dispatch import (  # noqa: PLC0415
        build_consensus_hook,
        build_premortem_hook,
    )
    from synthorg.communication.meeting.protocol_factory import (  # noqa: PLC0415
        build_protocol_factories,
    )

    consensus_policy, premortem_policy = await asyncio.gather(
        _resolve_consensus_policy(resolver),
        _resolve_premortem_policy(resolver),
    )
    return build_protocol_factories(
        consensus_hook=build_consensus_hook(consensus_policy),
        premortem_hook=build_premortem_hook(premortem_policy),
    )


async def wire_meeting_protocol_registry(app_state: AppState) -> None:
    """Install the meeting protocol registry on the orchestrator.

    Args:
        app_state: Application state carrying the orchestrator + resolver.
    """
    resolver = config_resolver_of(app_state)
    registry = await build_protocol_registry(resolver)
    _orchestrator(app_state).set_protocol_registry(registry)
    logger.info(
        API_SERVICE_AUTO_WIRED,
        service="meeting_protocol_registry",
        protocol_count=len(registry),
    )


async def unwire_meeting_protocol_registry(app_state: AppState) -> None:
    """Uninstall the meeting protocol registry.

    Args:
        app_state: Application state carrying the orchestrator.
    """
    orchestrator = app_state.slice(CommunicationStateSlice).meeting_orchestrator
    if orchestrator is None:
        # The reconciler also calls deactivate when a required capability
        # has gone away, so an absent orchestrator is reachable rather
        # than impossible. Saying so is the difference between "there was
        # nothing to tear down" and a teardown that quietly did not run.
        logger.warning(
            API_APP_STARTUP,
            service="meeting_protocol_registry",
            note="no orchestrator installed; nothing to unwire",
        )
        return
    orchestrator.clear_protocol_registry()
    logger.info(
        API_APP_STARTUP,
        service="meeting_protocol_registry",
        note="unwired",
    )
