# module-kind: feature
"""Engine feature manifest (engine core / work pipeline).

Declares the engine feature's surface: its ``engine`` settings
namespace and the :class:`EngineStateSlice` (task engine, work
pipeline, workflow services, entry adapters, etc.). The nested
``engine/cockpit`` and ``engine/brownfield`` packages declare their own
manifests. Controllers stay hand-wired in ``api/app.py``; this manifest
is declarative and feeds the navigation index.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.engine.state import EngineStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="engine",
    settings_namespace=SettingNamespace.ENGINE,
    state_slice=EngineStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(
        "AgentEngine",
        "IntakeEngine",
        "DirectIntake",
        "AgentIntake",
        "IntakeEntryAdapter",
        "build_work_entry_adapter",
        "TaskBoardEntryAdapter",
        "ObjectiveEntryAdapter",
        "BrownfieldEntryAdapter",
        "build_brownfield_entry_adapter",
        "BrownfieldImportService",
        "BrownfieldSourceResolver",
        "build_structure_map_scanners",
        "wire_real_brownfield_entry",
        "build_mcp_self_consumer",
        "build_coordinator",
        "build_stakes_router",
        "build_work_pipeline",
        "ForecastGate",
    ),
    depends_on=(),
)
