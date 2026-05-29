# module-kind: feature
"""Coordination feature manifest.

Declares the coordination feature's surface: its settings namespace,
state slice (the coordination-metrics store), the coordination REST
controllers, and the coordination MCP domain. The store is constructed
at app build time; the feature has no ghost-wired symbols here.
"""

from collections.abc import Mapping

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.coordination import CoordinationController
from synthorg.api.controllers.coordination_metrics import (
    CoordinationMetricsController,
)
from synthorg.coordination.state import CoordinationStateSlice
from synthorg.meta.mcp.domains.coordination import COORDINATION_TOOLS
from synthorg.meta.mcp.feature_descriptors import mcp_descriptor
from synthorg.settings.enums import SettingNamespace


def _coordination_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the coordination MCP handler map.

    Returns:
        The coordination ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.coordination import (  # noqa: PLC0415
        COORDINATION_HANDLERS,
    )

    return COORDINATION_HANDLERS


FEATURE: FeatureModule = FeatureManifest(
    name="coordination",
    settings_namespace=SettingNamespace.COORDINATION,
    state_slice=CoordinationStateSlice,
    controllers=(CoordinationController, CoordinationMetricsController),
    mcp_handlers=(
        mcp_descriptor(
            domain="coordination",
            tool_defs=COORDINATION_TOOLS,
            handlers=_coordination_mcp_handlers,
        ),
    ),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
