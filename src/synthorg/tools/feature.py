# module-kind: feature
"""Tools feature manifest.

Declares the tools feature's surface: its settings namespace and state
slice (the tool-invocation tracker). The agent tool registry itself is
built per-task by the engine, so the feature exposes no controller, MCP
domain, or ghost-wired symbols here.
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
