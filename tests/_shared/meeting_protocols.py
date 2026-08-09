"""Protocol-registry doubles for meeting orchestration tests.

The orchestrator's registry holds factories, so it builds a protocol per
meeting from that meeting's own configuration. A test that needs to
observe one specific instance (a mock it asserts against, or a protocol
it configured by hand) pins that instance here.

Deriving the registry from the config under test is what let the
production wiring gap go unobserved, so a test asserting that a
sub-config is honoured should drive ``build_protocol_factories``
instead of pinning.
"""

from collections.abc import Mapping

from synthorg.communication.meeting.config import MeetingProtocolConfig
from synthorg.communication.meeting.enums import MeetingProtocolType
from synthorg.communication.meeting.protocol import (
    MeetingProtocol,
    MeetingProtocolFactory,
)


def pin_protocol(protocol: MeetingProtocol) -> MeetingProtocolFactory:
    """Return a factory that always yields *protocol*.

    Returns:
        A factory ignoring the per-meeting configuration.
    """

    def _factory(_config: MeetingProtocolConfig) -> MeetingProtocol:
        return protocol

    return _factory


def pinned_protocol_registry(
    protocols: Mapping[MeetingProtocolType, MeetingProtocol],
) -> dict[MeetingProtocolType, MeetingProtocolFactory]:
    """Build a factory registry serving fixed protocol instances.

    Returns:
        A registry mapping each supplied type to a pinning factory.
    """
    return {
        protocol_type: pin_protocol(protocol)
        for protocol_type, protocol in protocols.items()
    }
