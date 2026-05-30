# module-kind: feature
"""Living-documentation feature manifest.

Declares the docs feature's surface: its state slice, REST controller, the
docs MCP domain, and the boot-constructed symbols the ghost-wiring gate
tracks. The docs subsystem has no dedicated settings namespace.
"""

from collections.abc import Mapping

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.project_docs import ProjectDocsController
from synthorg.docs_engine.state import DocsStateSlice
from synthorg.meta.mcp.domains.docs import DOCS_TOOLS
from synthorg.meta.mcp.feature_descriptors import mcp_descriptor


def _docs_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the docs MCP handler map.

    Returns:
        The docs ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.docs import DOCS_HANDLERS  # noqa: PLC0415

    return DOCS_HANDLERS


FEATURE: FeatureModule = FeatureManifest(
    name="docs",
    settings_namespace=None,
    state_slice=DocsStateSlice,
    controllers=(ProjectDocsController,),
    mcp_handlers=(
        mcp_descriptor(
            domain="docs",
            tool_defs=DOCS_TOOLS,
            handlers=_docs_mcp_handlers,
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
