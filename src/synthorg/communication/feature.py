# module-kind: feature
"""Communication feature manifest.

Declares the communication feature's surface: its ``communication``
settings namespace and the :class:`CommunicationStateSlice` (message
bus + service, meetings, event stream, escalation stack). Controllers
stay hand-wired in ``api/app.py``; this manifest is declarative and
feeds the navigation index.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.communication.state import CommunicationStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="communication",
    settings_namespace=SettingNamespace.COMMUNICATION,
    state_slice=CommunicationStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
