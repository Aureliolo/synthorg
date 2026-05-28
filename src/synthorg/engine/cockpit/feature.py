# module-kind: feature
"""Cockpit (mission-control) feature manifest.

Declares the cockpit feature's surface: its state slice, REST controller,
MCP tools, and the boot-constructed symbols the ghost-wiring gate tracks.
"""

from synthorg._core.features import (
    FeatureManifest,
    FeatureModule,
    McpHandlerDescriptor,
)
from synthorg.api.controllers.cockpit import CockpitController
from synthorg.engine.cockpit.state import CockpitStateSlice
from synthorg.meta.mcp.domains.cockpit import COCKPIT_TOOLS
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="cockpit",
    settings_namespace=SettingNamespace.COCKPIT,
    state_slice=CockpitStateSlice,
    controllers=(CockpitController,),
    mcp_handlers=(
        McpHandlerDescriptor(
            domain="cockpit",
            tool_names=tuple(tool.name for tool in COCKPIT_TOOLS),
        ),
    ),
    lifecycle_hooks=(),
    ghost_wired_symbols=(
        "CockpitService",
        "FlightRecorderService",
        "build_steering_directive",
        "build_flight_recorder_sink",
    ),
    depends_on=(),
)
