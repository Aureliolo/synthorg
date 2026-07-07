# module-kind: declarative
"""Read models for the Kanban board projection.

The board is a pure projection of task status onto columns (see
``STATUS_TO_COLUMN``); these frozen models carry a column's cards plus its
WIP-limit state so a single API response fully describes the board a
dashboard renders.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.task import Task
from synthorg.engine.workflow.enums import WorkflowType
from synthorg.engine.workflow.kanban_columns import KanbanColumn

#: Display order of the columns, left to right.
BOARD_COLUMN_ORDER: tuple[KanbanColumn, ...] = (
    KanbanColumn.BACKLOG,
    KanbanColumn.READY,
    KanbanColumn.IN_PROGRESS,
    KanbanColumn.REVIEW,
    KanbanColumn.DONE,
)


class KanbanColumnView(BaseModel):
    """A single board column with its cards and WIP-limit state.

    Attributes:
        column: The column this view describes.
        tasks: Cards currently in the column (tasks whose status maps here).
        count: Number of cards in the column.
        limit: Configured WIP limit, or ``None`` when the column is
            unlimited (backlog / ready / done).
        over_limit: ``True`` when ``count`` exceeds ``limit``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    column: KanbanColumn = Field(description="Board column")
    tasks: tuple[Task, ...] = Field(description="Cards in this column")
    count: int = Field(ge=0, description="Card count")
    limit: int | None = Field(default=None, description="WIP limit (None = unlimited)")
    over_limit: bool = Field(
        default=False,
        description="Whether the column is over its WIP limit",
    )


class KanbanBoardView(BaseModel):
    """The full board: ordered columns plus the active WIP policy.

    Attributes:
        columns: Columns in :data:`BOARD_COLUMN_ORDER`.
        workflow_type: The org's declared workflow type.
        enforce_wip: Whether WIP limits hard-block human moves (advisory
            when ``False``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    columns: tuple[KanbanColumnView, ...] = Field(description="Ordered columns")
    workflow_type: WorkflowType = Field(description="Active workflow type")
    enforce_wip: bool = Field(description="Whether WIP limits hard-block moves")
