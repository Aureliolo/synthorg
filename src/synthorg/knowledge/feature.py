# module-kind: feature
"""Knowledge + provenance substrate feature manifest.

Declares the knowledge feature's surface: its state slice, REST controllers,
the knowledge MCP domain, the ``knowledge`` settings namespace (runtime
synthesis knobs), and the boot-constructed symbols the ghost-wiring gate
tracks. The retrieval surface is gated on persistence + a memory backend; the
generative ``ask`` surface is additionally gated on a configured synthesis
model under the ``knowledge`` namespace.
"""

from collections.abc import Mapping

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.project_knowledge import (
    GlobalKnowledgeController,
    ProjectKnowledgeController,
)
from synthorg.knowledge.state import KnowledgeStateSlice
from synthorg.meta.mcp.domains.knowledge import KNOWLEDGE_TOOLS
from synthorg.meta.mcp.feature_descriptors import mcp_descriptor
from synthorg.settings.enums import SettingNamespace


def _knowledge_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the knowledge MCP handler map.

    Returns:
        The knowledge ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.knowledge import KNOWLEDGE_HANDLERS  # noqa: PLC0415

    return KNOWLEDGE_HANDLERS


FEATURE: FeatureModule = FeatureManifest(
    name="knowledge",
    settings_namespace=SettingNamespace.KNOWLEDGE,
    state_slice=KnowledgeStateSlice,
    controllers=(ProjectKnowledgeController, GlobalKnowledgeController),
    mcp_handlers=(
        mcp_descriptor(
            domain="knowledge",
            tool_defs=KNOWLEDGE_TOOLS,
            handlers=_knowledge_mcp_handlers,
        ),
    ),
    lifecycle_hooks=(),
    ghost_wired_symbols=(
        "build_knowledge_service",
        "KnowledgeService",
        "KnowledgeIndexer",
        "KnowledgeRetriever",
        "build_knowledge_tool_factory",
        "SearchKnowledgeTool",
        "IngestKnowledgeTool",
    ),
    depends_on=(),
)
