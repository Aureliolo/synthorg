# module-kind: feature
"""Cockpit (mission-control) feature manifest.

Declares the cockpit feature's surface: its state slice, REST controller,
the cockpit MCP domain, and the boot-constructed symbols the ghost-wiring
gate tracks.
"""

from collections.abc import Mapping

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.cockpit import CockpitController
from synthorg.engine.cockpit.state import CockpitStateSlice
from synthorg.meta.mcp.domains.cockpit import COCKPIT_TOOLS
from synthorg.meta.mcp.feature_descriptors import mcp_descriptor
from synthorg.settings.enums import SettingNamespace


def _cockpit_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the cockpit MCP handler map.

    Returns:
        The cockpit ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.cockpit import COCKPIT_HANDLERS  # noqa: PLC0415

    return COCKPIT_HANDLERS


FEATURE: FeatureModule = FeatureManifest(
    name="cockpit",
    settings_namespace=SettingNamespace.COCKPIT,
    state_slice=CockpitStateSlice,
    controllers=(CockpitController,),
    mcp_handlers=(
        mcp_descriptor(
            domain="cockpit",
            tool_defs=COCKPIT_TOOLS,
            handlers=_cockpit_mcp_handlers,
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
