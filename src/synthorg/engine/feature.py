# module-kind: feature
"""Engine feature manifest (engine core / work pipeline).

Declares the engine feature's surface: its ``engine`` settings
namespace, the :class:`EngineStateSlice` (task engine, work pipeline,
workflow services, entry adapters, etc.), and its core work-pipeline
REST controllers (projects, tasks, workflows, workflow versions /
executions, subworkflows, evaluation-config versions) mounted by the
composition root. The objective and brownfield controllers mount only
when their work-entry adapter is wired (claimed with predicates in a
later step). The nested ``engine/cockpit`` and ``engine/workspace``
packages declare their own manifests.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.evaluation_config_versions import (
    EvaluationConfigVersionController,
)
from synthorg.api.controllers.projects import ProjectController
from synthorg.api.controllers.subworkflows import SubworkflowController
from synthorg.api.controllers.tasks import TaskController
from synthorg.api.controllers.workflow_executions import (
    WorkflowExecutionController,
)
from synthorg.api.controllers.workflow_versions import WorkflowVersionController
from synthorg.api.controllers.workflows import WorkflowController
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
        WorkflowVersionController,
        WorkflowExecutionController,
        SubworkflowController,
        EvaluationConfigVersionController,
    ),
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
