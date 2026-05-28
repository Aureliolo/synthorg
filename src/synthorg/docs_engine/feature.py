# module-kind: feature
"""Living-documentation feature manifest.

Declares the docs feature's surface: its state slice, REST controller, MCP
tools, and the boot-constructed symbols the ghost-wiring gate tracks. The
docs subsystem has no dedicated settings namespace.
"""

from synthorg._core.features import (
    FeatureManifest,
    FeatureModule,
    McpHandlerDescriptor,
)
from synthorg.api.controllers.project_docs import ProjectDocsController
from synthorg.docs_engine.state import DocsStateSlice
from synthorg.meta.mcp.domains.docs import DOCS_TOOLS

FEATURE: FeatureModule = FeatureManifest(
    name="docs",
    settings_namespace=None,
    state_slice=DocsStateSlice,
    controllers=(ProjectDocsController,),
    mcp_handlers=(
        McpHandlerDescriptor(
            domain="docs",
            tool_names=tuple(tool.name for tool in DOCS_TOOLS),
        ),
    ),
    lifecycle_hooks=(),
    ghost_wired_symbols=(
        "build_docs_service",
        "DocsService",
        "DocChunker",
        "DocIndexer",
        "DocWriter",
        "ProjectAwareMemoryFacade",
        "DocsToolFactory",
        "WriteLivingDocTool",
        "SearchLivingDocsTool",
    ),
    depends_on=(),
)
