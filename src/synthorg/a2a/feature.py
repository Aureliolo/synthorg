# module-kind: feature
"""A2A (agent-to-agent federation) feature manifest.

Declares the A2A feature's surface: its settings namespace and state slice.
The gateway routes (Agent Card, JWKS, RPC) are registered as standalone
route handlers rather than a controller class, and the A2A collaborators
are constructed directly at boot, so the feature has no controller, MCP
domain, or ghost-wired symbols.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.a2a.state import A2aStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="a2a",
    settings_namespace=SettingNamespace.A2A,
    state_slice=A2aStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
