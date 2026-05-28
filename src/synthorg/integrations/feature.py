# module-kind: feature
"""Integrations feature manifest.

Declares the integrations feature's surface: its ``integrations``
settings namespace and the :class:`IntegrationsStateSlice` (connections,
OAuth, webhooks, tunnel, MCP catalog, health prober). Controllers stay
hand-wired in ``api/app.py``; this manifest is declarative and feeds
the navigation index.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="integrations",
    settings_namespace=SettingNamespace.INTEGRATIONS,
    state_slice=IntegrationsStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
