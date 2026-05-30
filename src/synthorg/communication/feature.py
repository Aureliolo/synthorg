# module-kind: feature
"""Communication feature manifest.

Declares the communication feature's surface: its ``communication``
settings namespace, the :class:`CommunicationStateSlice` (message
bus + service, meetings, event stream, escalation stack), its REST
controllers (messages, meetings, ceremony policy, event stream,
interrupts, escalations), and the communication MCP domain mounted by
the composition root.
"""

from collections.abc import Mapping

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.ceremony_policy import CeremonyPolicyController
from synthorg.api.controllers.escalations import EscalationsController
from synthorg.api.controllers.events import (
    EventStreamController,
    InterruptController,
)
from synthorg.api.controllers.meetings import MeetingController
from synthorg.api.controllers.messages import MessageController
from synthorg.communication._construction import wire_construction
from synthorg.communication.state import CommunicationStateSlice
from synthorg.meta.mcp.domains.communication import COMMUNICATION_TOOLS
from synthorg.meta.mcp.feature_descriptors import mcp_descriptor
from synthorg.settings.enums import SettingNamespace


def _communication_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the communication MCP handler map.

    Returns:
        The communication ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.communication import (  # noqa: PLC0415
        COMMUNICATION_HANDLERS,
    )

    return COMMUNICATION_HANDLERS


FEATURE: FeatureModule = FeatureManifest(
    name="communication",
    settings_namespace=SettingNamespace.COMMUNICATION,
    state_slice=CommunicationStateSlice,
    controllers=(
        MessageController,
        MeetingController,
        CeremonyPolicyController,
        EventStreamController,
        InterruptController,
        EscalationsController,
    ),
    mcp_handlers=(
        mcp_descriptor(
            domain="communication",
            tool_defs=COMMUNICATION_TOOLS,
            handlers=_communication_mcp_handlers,
        ),
    ),
    lifecycle_hooks=(),
    construction_wirer=wire_construction,
    ghost_wired_symbols=(),
    depends_on=(),
)
