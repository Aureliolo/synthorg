# module-kind: feature
"""Engine feature manifest (engine core / work pipeline).

Declares the engine feature's surface: its ``engine`` settings
namespace, the :class:`EngineStateSlice` (task engine, work pipeline,
workflow services, entry adapters, etc.), its core work-pipeline
REST controllers (projects, tasks, workflows, workflow versions /
executions, subworkflows, evaluation-config versions), and the tasks +
workflows MCP domains (assembled in ``engine/_mcp.py``) mounted by the
composition root. The objective and brownfield controllers mount
unconditionally; their work-entry adapters wire during startup (after
route assembly), so a predicate read at mount time would always be False
and they would never mount on a standard boot. Their handlers resolve
the adapter via ``require_service`` and return 503 until it is wired.
The nested ``engine/cockpit`` and ``engine/workspace`` packages declare
their own manifests.
"""

from synthorg._core.features import (
    FeatureManifest,
    FeatureModule,
)
from synthorg.api.controllers.brownfield import BrownfieldController
from synthorg.api.controllers.decomposition import DecompositionController
from synthorg.api.controllers.evaluation_config_versions import (
    EvaluationConfigVersionController,
)
from synthorg.api.controllers.objectives import ObjectiveController
from synthorg.api.controllers.projects import ProjectController
from synthorg.api.controllers.subworkflows import SubworkflowController
from synthorg.api.controllers.tasks import TaskController
from synthorg.api.controllers.workflow_executions import WorkflowExecutionController
from synthorg.api.controllers.workflow_versions import WorkflowVersionController
from synthorg.api.controllers.workflows.blueprints import WorkflowBlueprintController
from synthorg.api.controllers.workflows.crud import WorkflowController
from synthorg.api.controllers.workflows.validation import WorkflowValidationController
from synthorg.engine._construction import wire_construction
from synthorg.engine._mcp import ENGINE_MCP_HANDLERS
from synthorg.engine.state import EngineStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="engine",
    settings_namespace=SettingNamespace.ENGINE,
    state_slice=EngineStateSlice,
    controllers=(
        ProjectController,
        TaskController,
        WorkflowController,
        WorkflowBlueprintController,
        WorkflowValidationController,
        WorkflowVersionController,
        WorkflowExecutionController,
        SubworkflowController,
        EvaluationConfigVersionController,
        DecompositionController,
        # Mounted unconditionally: their work-entry adapters wire during
        # startup (after route assembly), so a predicate read at mount
        # time would always be False and the controller would never mount
        # on a standard boot. The handlers resolve the adapter via
        # ``require_service`` and return 503 until it is wired.
        ObjectiveController,
        BrownfieldController,
    ),
    mcp_handlers=ENGINE_MCP_HANDLERS,
    lifecycle_hooks=(),
    construction_wirer=wire_construction,
    ghost_wired_symbols=(
        "EvaluationVersionService",
        "SubworkflowService",
        "build_evolution_service",
        "InMemoryErrorTaxonomyStore",
        "PerformanceTrackerSink",
        "NotificationDispatcherSink",
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
        "ManualDecompositionStrategy",
        "build_stakes_router",
        "build_work_pipeline",
        "ForecastGate",
    ),
    depends_on=(),
)
