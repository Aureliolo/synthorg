# module-kind: feature
"""Facades feature manifest.

Declares the read / MCP facade family: the :class:`FacadesStateSlice`
that owns the dashboard / MCP read facades aggregating domain services.
The facade family has no dedicated settings namespace. Controllers and
MCP handlers stay hand-wired; this manifest is declarative and feeds
the navigation index.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.infrastructure.state import FacadesStateSlice

FEATURE: FeatureModule = FeatureManifest(
    name="facades",
    settings_namespace=None,
    state_slice=FacadesStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
