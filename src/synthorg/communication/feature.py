# module-kind: feature
"""Communication feature manifest.

Declares the communication feature's surface: its ``communication``
settings namespace, the :class:`CommunicationStateSlice` (message
bus + service, meetings, event stream, escalation stack), and its REST
controllers (messages, meetings, ceremony policy, event stream,
interrupts, escalations) mounted by the composition root.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.ceremony_policy import CeremonyPolicyController
from synthorg.api.controllers.escalations import EscalationsController
from synthorg.api.controllers.events import (
    EventStreamController,
    InterruptController,
)
from synthorg.api.controllers.meetings import MeetingController
from synthorg.api.controllers.messages import MessageController
from synthorg.communication.state import CommunicationStateSlice
from synthorg.settings.enums import SettingNamespace

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
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
