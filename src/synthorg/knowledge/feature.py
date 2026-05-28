# module-kind: feature
"""Knowledge + provenance substrate feature manifest.

Declares the knowledge feature's surface: its state slice, REST controllers,
MCP tools, and the boot-constructed symbols the ghost-wiring gate tracks.
The knowledge feature has no dedicated settings namespace (it is gated on
persistence + a memory backend, not operator settings).
"""

from synthorg._core.features import (
    FeatureManifest,
    FeatureModule,
    McpHandlerDescriptor,
)
from synthorg.api.controllers.project_knowledge import (
    GlobalKnowledgeController,
    ProjectKnowledgeController,
)
from synthorg.knowledge.state import KnowledgeStateSlice
from synthorg.meta.mcp.domains.knowledge import KNOWLEDGE_TOOLS

FEATURE: FeatureModule = FeatureManifest(
    name="knowledge",
    settings_namespace=None,
    state_slice=KnowledgeStateSlice,
    controllers=(ProjectKnowledgeController, GlobalKnowledgeController),
    mcp_handlers=(
        McpHandlerDescriptor(
            domain="knowledge",
            tool_names=tuple(tool.name for tool in KNOWLEDGE_TOOLS),
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
