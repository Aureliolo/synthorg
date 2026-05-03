"""Umbrella workflow configuration model.

Aggregates the per-workflow-type configs (Kanban, Sprint) under a
single ``WorkflowConfig`` that plugs into the root configuration.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import WorkflowType
from synthorg.engine.workflow.kanban_board import KanbanConfig
from synthorg.engine.workflow.sprint_config import SprintConfig
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import WORKFLOW_CONFIG_UNUSED_SUBCONFIG

logger = get_logger(__name__)

# Workflow types that consult kanban sub-config at runtime.
_KANBAN_TYPES: frozenset[WorkflowType] = frozenset(
    {WorkflowType.KANBAN, WorkflowType.AGILE_KANBAN},
)
# Workflow types that consult sprint sub-config at runtime.
_SPRINT_TYPES: frozenset[WorkflowType] = frozenset(
    {WorkflowType.AGILE_KANBAN},
)


class WorkflowConfig(BaseModel):
    """Top-level workflow configuration.

    Both ``kanban`` and ``sprint`` sub-configs are always present for
    convenience, but only the sub-config relevant to the active
    ``workflow_type`` is consulted by the engine at runtime.

    Attributes:
        workflow_type: Active workflow type.
        kanban: Kanban board settings (consulted by the engine when
            workflow_type is ``KANBAN`` or ``AGILE_KANBAN``).
        sprint: Agile sprint settings (consulted by the engine when
            workflow_type is ``AGILE_KANBAN``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    workflow_type: WorkflowType = Field(
        default=WorkflowType.AGILE_KANBAN,
        description="Active workflow type",
    )
    kanban: KanbanConfig = Field(
        default_factory=KanbanConfig,
        description="Kanban board settings",
    )
    sprint: SprintConfig = Field(
        default_factory=SprintConfig,
        description="Agile sprint settings",
    )

    @model_validator(mode="after")
    def _warn_unused_subconfigs(self) -> Self:
        """Log advisory warning when sub-configs are customized but unused."""
        if self.workflow_type not in _KANBAN_TYPES and self.kanban != KanbanConfig():
            logger.warning(
                WORKFLOW_CONFIG_UNUSED_SUBCONFIG,
                reason="kanban_config_unused",
                workflow_type=self.workflow_type.value,
            )
        if self.workflow_type not in _SPRINT_TYPES and self.sprint != SprintConfig():
            logger.warning(
                WORKFLOW_CONFIG_UNUSED_SUBCONFIG,
                reason="sprint_config_unused",
                workflow_type=self.workflow_type.value,
            )
        return self
