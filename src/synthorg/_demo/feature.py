# module-kind: feature
"""Demo feature manifest: the substrate's end-to-end discovery guard.

Declares the demo feature's whole surface from its own directory: the ``demo``
settings namespace, the :class:`DemoStateSlice`, its construction wirer, the
single REST controller, and the single MCP tool. Discovered at boot like every
other feature, it proves a new feature is reachable end-to-end with zero edits
to ``api/app.py`` / ``api/state.py`` / any central wiring. See ADR-0008.
"""

from collections.abc import Mapping

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg._demo._construction import wire_construction
from synthorg._demo.controller import DemoController
from synthorg._demo.mcp import DEMO_TOOLS
from synthorg._demo.state import DemoStateSlice
from synthorg.meta.mcp.feature_descriptors import mcp_descriptor
from synthorg.settings.enums import SettingNamespace


def _demo_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the demo MCP handler map.

    Returns:
        The demo ``{tool_name: ToolHandler}`` map.
    """
    from synthorg._demo.mcp import DEMO_HANDLERS  # noqa: PLC0415

    return DEMO_HANDLERS


FEATURE: FeatureModule = FeatureManifest(
    name="demo",
    settings_namespace=SettingNamespace.DEMO,
    state_slice=DemoStateSlice,
    controllers=(DemoController,),
    mcp_handlers=(
        mcp_descriptor(
            domain="demo",
            tool_defs=DEMO_TOOLS,
            handlers=_demo_mcp_handlers,
        ),
    ),
    lifecycle_hooks=(),
    construction_wirer=wire_construction,
    ghost_wired_symbols=(),
    depends_on=(),
)
