# module-kind: feature
"""Long-horizon project-brain feature manifest.

Declares the brain feature's surface: its state slice, REST controller, MCP
tools, and the boot-constructed symbols the ghost-wiring gate tracks. The brain
subsystem has no dedicated settings namespace; its retrieval leg rides the docs
engine's shared :class:`ProjectAwareMemoryFacade`, so the facade is not declared
here (the docs feature owns it).
"""

from collections.abc import Mapping

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.project_brain import ProjectBrainController
from synthorg.meta.mcp.domains.brain import BRAIN_TOOLS
from synthorg.meta.mcp.feature_descriptors import mcp_descriptor
from synthorg.project_brain.state import ProjectBrainStateSlice


def _brain_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the brain MCP handler map.

    Returns:
        The brain ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.brain import BRAIN_HANDLERS  # noqa: PLC0415

    return BRAIN_HANDLERS


FEATURE: FeatureModule = FeatureManifest(
    name="project_brain",
    settings_namespace=None,
    state_slice=ProjectBrainStateSlice,
    controllers=(ProjectBrainController,),
    mcp_handlers=(
        mcp_descriptor(
            domain="brain",
            tool_defs=BRAIN_TOOLS,
            handlers=_brain_mcp_handlers,
        ),
    ),
    lifecycle_hooks=(),
    ghost_wired_symbols=(
        "build_project_brain_service",
        "ProjectBrainService",
        "BrainChunker",
        "BrainIndexer",
        "BrainWriter",
        "ProjectBrainToolFactory",
        "WriteBrainEntryTool",
        "SearchBrainTool",
    ),
    depends_on=(),
)
