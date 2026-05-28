# module-kind: feature
"""Tools feature manifest.

Declares the tools feature's surface: its settings namespace, state
slice (the tool-invocation tracker), and ghost-wired symbols owned by
the feature (the boot-time parity check expects them here). The agent
tool registry itself is built per-task by the engine, so the feature
exposes no controller or MCP domain.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.settings.enums import SettingNamespace
from synthorg.tools.state import ToolsStateSlice

FEATURE: FeatureModule = FeatureManifest(
    name="tools",
    settings_namespace=SettingNamespace.TOOLS,
    state_slice=ToolsStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(
        "BrowserTool",
        "SSIMDiffer",
        "WorkspaceBaselineStore",
        "build_structure_map_tool_factory",
        "QueryStructureMapTool",
        "create_lifecycle_strategy",
    ),
    depends_on=(),
)
