"""Engine feature state slice (engine core / work pipeline).

Holds the task engine, work pipeline, workflow services (definition,
version, execution, subworkflow), evaluation version service, the
ceremony scheduler, and the work-entry adapters (intake / objective /
brownfield / task board). All fields are ``None`` until wired; readers
guard accordingly. The workflow rollback service lives on the api-core
slice (it is an api-layer service) to keep this package free of an api
dependency; the steering directive and flight recorder are owned by the
cockpit slice (``CockpitStateSlice``), the only reader of either.
"""

from typing import TYPE_CHECKING, Any

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.engine.pipeline.entry.protocol import WorkEntryAdapter  # noqa: TC001
from synthorg.engine.pipeline.entry.task_board_adapter import (
    TaskBoardEntryAdapter,  # noqa: TC001
)
from synthorg.engine.pipeline.protocol import WorkPipeline  # noqa: TC001
from synthorg.engine.quality.mcp_services import (
    EvaluationVersionService,  # noqa: TC001
)
from synthorg.engine.task_engine import TaskEngine  # noqa: TC001
from synthorg.engine.workflow.ceremony_scheduler import (
    CeremonyScheduler,  # noqa: TC001
)
from synthorg.engine.workflow.execution_service import (
    WorkflowExecutionService,  # noqa: TC001
)
from synthorg.engine.workflow.service import WorkflowService  # noqa: TC001
from synthorg.engine.workflow.subworkflow_service import (
    SubworkflowService,  # noqa: TC001
)
from synthorg.engine.workflow.version_service import (
    WorkflowVersionService,  # noqa: TC001
)
from synthorg.tools.structure_map.tool_factory import (
    StructureMapToolFactory,  # noqa: TC001
)

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin


class EngineStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the engine feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_engine: TaskEngine | None = None
    work_pipeline: WorkPipeline | None = None
    workflow_service: WorkflowService | None = None
    workflow_version_service: WorkflowVersionService | None = None
    workflow_execution_service: WorkflowExecutionService | None = None
    subworkflow_service: SubworkflowService | None = None
    evaluation_version_service: EvaluationVersionService | None = None
    ceremony_scheduler: CeremonyScheduler | None = None
    intake_entry_adapter: WorkEntryAdapter[Any] | None = None
    objective_entry_adapter: WorkEntryAdapter[Any] | None = None
    brownfield_entry_adapter: WorkEntryAdapter[Any] | None = None
    task_board_entry_adapter: TaskBoardEntryAdapter | None = None
    structure_map_tool_factory: StructureMapToolFactory | None = None


def task_engine_of(app_state: AppStateSliceMixin) -> TaskEngine:
    """Resolve the task engine from its slice, or raise 503.

    Returns:
        The wired task engine.
    """
    return require_service(app_state.slice(EngineStateSlice).task_engine, "Task Engine")


def work_pipeline_of(app_state: AppStateSliceMixin) -> WorkPipeline:
    """Resolve the work pipeline from its slice, or raise 503.

    Returns:
        The wired work pipeline.
    """
    return require_service(
        app_state.slice(EngineStateSlice).work_pipeline, "Work Pipeline"
    )


def workflow_service_of(app_state: AppStateSliceMixin) -> WorkflowService:
    """Resolve the workflow service from its slice, or raise 503.

    Returns:
        The wired workflow service.
    """
    return require_service(
        app_state.slice(EngineStateSlice).workflow_service, "Workflow Service"
    )


def workflow_version_service_of(
    app_state: AppStateSliceMixin,
) -> WorkflowVersionService:
    """Resolve the workflow version service from its slice, or raise 503.

    Returns:
        The wired workflow version service.
    """
    return require_service(
        app_state.slice(EngineStateSlice).workflow_version_service,
        "Workflow Version Service",
    )


def workflow_execution_service_of(
    app_state: AppStateSliceMixin,
) -> WorkflowExecutionService:
    """Resolve the workflow execution service from its slice, or raise 503.

    Returns:
        The wired workflow execution service.
    """
    return require_service(
        app_state.slice(EngineStateSlice).workflow_execution_service,
        "Workflow Execution Service",
    )


def subworkflow_service_of(app_state: AppStateSliceMixin) -> SubworkflowService:
    """Resolve the subworkflow service from its slice, or raise 503.

    Returns:
        The wired subworkflow service.
    """
    return require_service(
        app_state.slice(EngineStateSlice).subworkflow_service, "Subworkflow Service"
    )


def evaluation_version_service_of(
    app_state: AppStateSliceMixin,
) -> EvaluationVersionService:
    """Resolve the evaluation version service from its slice, or raise 503.

    Returns:
        The wired evaluation version service.
    """
    return require_service(
        app_state.slice(EngineStateSlice).evaluation_version_service,
        "Evaluation Version Service",
    )


def intake_entry_adapter_of(app_state: AppStateSliceMixin) -> WorkEntryAdapter[Any]:
    """Resolve the intake entry adapter from its slice, or raise 503.

    Returns:
        The wired intake entry adapter.
    """
    return require_service(
        app_state.slice(EngineStateSlice).intake_entry_adapter,
        "Intake Entry Adapter",
    )


def objective_entry_adapter_of(
    app_state: AppStateSliceMixin,
) -> WorkEntryAdapter[Any]:
    """Resolve the objective entry adapter from its slice, or raise 503.

    Returns:
        The wired objective entry adapter.
    """
    return require_service(
        app_state.slice(EngineStateSlice).objective_entry_adapter,
        "Objective Entry Adapter",
    )


def brownfield_entry_adapter_of(
    app_state: AppStateSliceMixin,
) -> WorkEntryAdapter[Any]:
    """Resolve the brownfield entry adapter from its slice, or raise 503.

    Returns:
        The wired brownfield entry adapter.
    """
    return require_service(
        app_state.slice(EngineStateSlice).brownfield_entry_adapter,
        "Brownfield Entry Adapter",
    )


def task_board_entry_adapter_of(
    app_state: AppStateSliceMixin,
) -> TaskBoardEntryAdapter:
    """Resolve the task board entry adapter from its slice, or raise 503.

    Returns:
        The wired task board entry adapter.
    """
    return require_service(
        app_state.slice(EngineStateSlice).task_board_entry_adapter,
        "Task Board Entry Adapter",
    )
