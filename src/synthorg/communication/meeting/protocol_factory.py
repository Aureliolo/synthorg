# module-kind: code
"""Per-meeting protocol construction (see Communication design page).

A protocol is built per meeting, from that meeting's own
:class:`MeetingProtocolConfig`. The alternative, one instance per
process built at wiring time, pins every meeting to whatever
configuration was in force at boot, so a sub-config an operator sets on
a meeting type reaches nothing.

Each factory reads only the sub-config matching the protocol it builds,
so the invariant :class:`MeetingProtocolConfig` documents lives in one
place rather than at every construction site.

The strategy hooks are wiring-time singletons the structured-phases
factory closes over: they derive from the org's strategy configuration
rather than from any one meeting, so rebuilding them per meeting would
cost work without changing an answer.
"""

from collections.abc import Mapping

from synthorg.communication.meeting.config import MeetingProtocolConfig
from synthorg.communication.meeting.enums import MeetingProtocolType
from synthorg.communication.meeting.errors import MeetingProtocolNotFoundError
from synthorg.communication.meeting.hooks import (
    ConsensusVelocityHook,
    PremortemHook,
)
from synthorg.communication.meeting.position_papers import PositionPapersProtocol
from synthorg.communication.meeting.protocol import (
    MeetingProtocol,
    MeetingProtocolFactory,
)
from synthorg.communication.meeting.round_robin import RoundRobinProtocol
from synthorg.communication.meeting.structured_phases import (
    StructuredPhasesProtocol,
)


def build_protocol_factories(
    *,
    consensus_hook: ConsensusVelocityHook | None = None,
    premortem_hook: PremortemHook | None = None,
) -> Mapping[MeetingProtocolType, MeetingProtocolFactory]:
    """Build one factory per declared meeting protocol type.

    Args:
        consensus_hook: Premature-consensus check the structured-phases
            protocol runs over gathered positions. ``None`` disables the
            velocity check.
        premortem_hook: Premortem analysis the structured-phases protocol
            folds into its synthesis summary. ``None`` disables the
            premortem phase.

    Returns:
        Mapping from protocol type to a factory taking that meeting's
        protocol configuration.

    Raises:
        MeetingProtocolNotFoundError: When a declared protocol type has
            no factory, which would leave meetings of that type
            unrunnable.
    """

    def _round_robin(config: MeetingProtocolConfig) -> MeetingProtocol:
        return RoundRobinProtocol(config.round_robin)

    def _position_papers(config: MeetingProtocolConfig) -> MeetingProtocol:
        return PositionPapersProtocol(config.position_papers)

    def _structured_phases(config: MeetingProtocolConfig) -> MeetingProtocol:
        return StructuredPhasesProtocol(
            config.structured_phases,
            consensus_hook=consensus_hook,
            premortem_hook=premortem_hook,
        )

    factories: dict[MeetingProtocolType, MeetingProtocolFactory] = {
        MeetingProtocolType.ROUND_ROBIN: _round_robin,
        MeetingProtocolType.POSITION_PAPERS: _position_papers,
        MeetingProtocolType.STRUCTURED_PHASES: _structured_phases,
    }
    missing = tuple(
        protocol_type
        for protocol_type in MeetingProtocolType
        if protocol_type not in factories
    )
    if missing:
        msg = f"No protocol factory registered for {missing!r}"
        raise MeetingProtocolNotFoundError(
            msg,
            context={"missing_protocol_types": [pt.value for pt in missing]},
        )
    return factories
