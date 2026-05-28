# module-kind: feature
"""Charter feature manifest.

Declares the charter feature's surface for the feature-manifest substrate:
its settings namespace, state slice, REST controller, MCP tools, and the
boot-constructed symbols the ghost-wiring gate tracks.
"""

from synthorg._core.features import (
    FeatureManifest,
    FeatureModule,
    McpHandlerDescriptor,
)
from synthorg.api.controllers.charter import CharterController
from synthorg.meta.charter.state import CharterStateSlice
from synthorg.meta.mcp.domains.charter import CHARTER_TOOLS
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="charter",
    settings_namespace=SettingNamespace.CHARTER,
    state_slice=CharterStateSlice,
    controllers=(CharterController,),
    mcp_handlers=(
        McpHandlerDescriptor(
            domain="charter",
            tool_names=tuple(tool.name for tool in CHARTER_TOOLS),
        ),
    ),
    lifecycle_hooks=(),
    ghost_wired_symbols=("CharterInterviewService", "CharterDispatcher"),
    depends_on=(),
)
