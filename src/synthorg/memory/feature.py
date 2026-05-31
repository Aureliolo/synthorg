# module-kind: feature
"""Memory feature manifest.

Declares the memory feature's surface: its settings namespace, state slice
(shared backend + fine-tune orchestrator), the per-sub-domain memory-admin
REST controllers (fine-tune, checkpoints, entries, embedder) each guarded
for the CEO / SYSTEM role, and the memory MCP domain. The backend is wired
during the training-service auto-wire path; the fine-tune orchestrator is
wired on startup by ``_wire_fine_tune_orchestrator`` once persistence
connects, so ``FineTuneOrchestrator`` is ghost-wired here.
"""

from collections.abc import Mapping

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.memory.checkpoints import MemoryCheckpointsController
from synthorg.api.controllers.memory.embedder import MemoryEmbedderController
from synthorg.api.controllers.memory.entries import MemoryEntriesController
from synthorg.api.controllers.memory.fine_tune import MemoryFineTuneController
from synthorg.memory.state import MemoryStateSlice
from synthorg.meta.mcp.domains.memory import MEMORY_TOOLS
from synthorg.meta.mcp.feature_descriptors import mcp_descriptor
from synthorg.settings.enums import SettingNamespace


def _memory_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the memory MCP handler map.

    Returns:
        The memory ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.memory import MEMORY_HANDLERS  # noqa: PLC0415

    return MEMORY_HANDLERS


FEATURE: FeatureModule = FeatureManifest(
    name="memory",
    settings_namespace=SettingNamespace.MEMORY,
    state_slice=MemoryStateSlice,
    controllers=(
        MemoryFineTuneController,
        MemoryCheckpointsController,
        MemoryEntriesController,
        MemoryEmbedderController,
    ),
    mcp_handlers=(
        mcp_descriptor(
            domain="memory",
            tool_defs=MEMORY_TOOLS,
            handlers=_memory_mcp_handlers,
        ),
    ),
    lifecycle_hooks=(),
    ghost_wired_symbols=("FineTuneOrchestrator",),
    depends_on=(),
)
