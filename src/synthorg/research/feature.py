# module-kind: feature
"""Research subsystem feature manifest.

Declares the research feature's surface: its state slice, MCP tools, and the
boot-constructed symbols the ghost-wiring gate tracks. Research has no REST
controller (its surface is the agent tool + MCP handlers).
"""

from synthorg._core.features import (
    FeatureManifest,
    FeatureModule,
    McpHandlerDescriptor,
)
from synthorg.meta.mcp.domains.research import RESEARCH_TOOLS
from synthorg.research.state import ResearchStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="research",
    settings_namespace=SettingNamespace.RESEARCH,
    state_slice=ResearchStateSlice,
    controllers=(),
    mcp_handlers=(
        McpHandlerDescriptor(
            domain="research",
            tool_names=tuple(tool.name for tool in RESEARCH_TOOLS),
        ),
    ),
    lifecycle_hooks=(),
    ghost_wired_symbols=(
        "build_research_service",
        "ResearchService",
        "LlmQueryPlanner",
        "HybridCredibilityTriage",
        "LexicalDeduplicator",
        "LlmSynthesizer",
        "KnowledgeRetrievalSource",
        "WebRetrievalSource",
        "AcademicRetrievalSource",
        "CodeRetrievalSource",
        "build_research_tool_factory",
        "ResearchTool",
    ),
    depends_on=(),
)
