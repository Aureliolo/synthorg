# module-kind: feature
"""Charter feature manifest.

Declares the charter feature's surface for the feature-manifest substrate:
its settings namespace, state slice, REST controller, the charter MCP
domain, and the boot-constructed symbols the ghost-wiring gate tracks.
"""

from collections.abc import Mapping

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.charter import CharterController
from synthorg.meta.charter.state import CharterStateSlice
from synthorg.meta.mcp.domains.charter import CHARTER_TOOLS
from synthorg.meta.mcp.feature_descriptors import mcp_descriptor
from synthorg.settings.enums import SettingNamespace


def _charter_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the charter MCP handler map.

    Returns:
        The charter ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.charter import CHARTER_HANDLERS  # noqa: PLC0415

    return CHARTER_HANDLERS


FEATURE: FeatureModule = FeatureManifest(
    name="charter",
    settings_namespace=SettingNamespace.CHARTER,
    state_slice=CharterStateSlice,
    controllers=(CharterController,),
    mcp_handlers=(
        mcp_descriptor(
            domain="charter",
            tool_defs=CHARTER_TOOLS,
            handlers=_charter_mcp_handlers,
        ),
    ),
    lifecycle_hooks=(),
    ghost_wired_symbols=("CharterInterviewService", "CharterDispatcher"),
    depends_on=(),
)
