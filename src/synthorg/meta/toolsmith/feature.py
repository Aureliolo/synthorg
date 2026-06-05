# module-kind: feature
"""Toolsmith (self-extending toolkit) feature manifest.

Declares the toolsmith feature's surface: its state slice and the
boot-constructed symbols the ghost-wiring gate tracks. The toolsmith has no
REST controller or MCP domain of its own (it operates at the TOOL_CREATION
altitude inside the engine and layers authored tools into the live invoker).
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.meta.toolsmith.state import ToolsmithStateSlice

FEATURE: FeatureModule = FeatureManifest(
    name="toolsmith",
    settings_namespace=None,
    state_slice=ToolsmithStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(
        "build_toolsmith",
        "ToolsmithService",
        "RingBufferCapabilityGapStore",
        "LLMToolBlueprintGenerator",
        "BenchmarkToolValidationGate",
        "EvalGoldenScorecardProvider",
        "SandboxBriefRunner",
        "ToolCreationApplier",
        "DynamicToolRegistry",
        "install_dynamic_tool_layer",
        "install_capability_gap_sink",
        "ToolsmithCycleScheduler",
    ),
    depends_on=(),
)
