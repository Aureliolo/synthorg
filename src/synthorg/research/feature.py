# module-kind: feature
"""Research subsystem feature manifest.

Declares the research feature's surface: its state slice, the research MCP
domain, and the boot-constructed symbols the ghost-wiring gate tracks.
Research has no REST controller (its surface is the agent tool + MCP
handlers).
"""

from collections.abc import Mapping

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.meta.mcp.domains.research import RESEARCH_TOOLS
from synthorg.meta.mcp.feature_descriptors import mcp_descriptor
from synthorg.research.state import ResearchStateSlice
from synthorg.settings.enums import SettingNamespace


def _research_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the research MCP handler map.

    Returns:
        The research ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.research import RESEARCH_HANDLERS  # noqa: PLC0415

    return RESEARCH_HANDLERS


FEATURE: FeatureModule = FeatureManifest(
    name="research",
    settings_namespace=SettingNamespace.RESEARCH,
    state_slice=ResearchStateSlice,
    controllers=(),
    mcp_handlers=(
        mcp_descriptor(
            domain="research",
            tool_defs=RESEARCH_TOOLS,
            handlers=_research_mcp_handlers,
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
