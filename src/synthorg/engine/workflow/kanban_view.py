# module-kind: declarative
"""Read models for the Kanban board projection.

The board is a pure projection of task status onto columns (see
``STATUS_TO_COLUMN``); these frozen models carry a column's cards plus its
WIP-limit state so a single API response fully describes the board a
dashboard renders.
"""

from pydantic import BaseModel, ConfigDict, Field, computed_field

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

    ``count`` and ``over_limit`` are derived from ``tasks`` + ``limit`` so a
    view can never be constructed with a count that disagrees with its cards.

    Attributes:
        column: The column this view describes.
        tasks: Cards currently in the column (tasks whose status maps here).
        limit: Configured WIP limit, or ``None`` when the column is
            unlimited (backlog / ready / done).
        count: Number of cards in the column (derived from ``tasks``).
        over_limit: ``True`` when ``count`` exceeds ``limit`` (derived).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    column: KanbanColumn = Field(description="Board column")
    tasks: tuple[Task, ...] = Field(description="Cards in this column")
    limit: int | None = Field(default=None, description="WIP limit (None = unlimited)")

    @computed_field(description="Card count")
    @property
    def count(self) -> int:
        """Number of cards currently in the column."""
        return len(self.tasks)

    @computed_field(description="Whether the column is over its WIP limit")
    @property
    def over_limit(self) -> bool:
        """Whether the card count exceeds the configured WIP limit."""
        return self.limit is not None and self.count > self.limit


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
