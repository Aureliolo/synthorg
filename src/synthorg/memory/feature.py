# module-kind: feature
"""Memory feature manifest.

Declares the memory feature's surface: its settings namespace, state slice
(shared backend + fine-tune orchestrator), and the per-sub-domain
memory-admin REST controllers (fine-tune, checkpoints, entries, embedder),
each guarded for the CEO / SYSTEM role. The backend is wired during the
training-service auto-wire path; the feature has no ghost-wired symbols of
its own here.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.memory.checkpoints import MemoryCheckpointsController
from synthorg.api.controllers.memory.embedder import MemoryEmbedderController
from synthorg.api.controllers.memory.entries import MemoryEntriesController
from synthorg.api.controllers.memory.fine_tune import MemoryFineTuneController
from synthorg.memory.state import MemoryStateSlice
from synthorg.settings.enums import SettingNamespace

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
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
