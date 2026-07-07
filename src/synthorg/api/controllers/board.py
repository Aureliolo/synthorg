"""Kanban board endpoints at /board.

Read the org's board (tasks projected onto columns with live WIP state) and
move a card to another column. Moves flow through the task engine, so the
board never bypasses the single-writer status machine.
"""

from litestar import Controller, get, post
from litestar.datastructures import State
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.controllers.tasks import _extract_requester
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.state import kanban_board_service_of
from synthorg.engine.workflow.kanban_columns import KanbanColumn
from synthorg.engine.workflow.kanban_view import KanbanBoardView


class BoardMovePayload(BaseModel):
    """Request to move a card to another board column."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task_id: NotBlankStr = Field(description="The card's task id")
    target_column: KanbanColumn = Field(description="Column to move the card into")


class BoardController(Controller):
    """Kanban board projection + column-move endpoints."""

    path = "/board"
    tags = ("board",)
    guards = [require_read_access]  # noqa: RUF012

    @get("/")
    async def get_board(
        self,
        state: State,
        project: str | None = None,
    ) -> ApiResponse[KanbanBoardView]:
        """Project current tasks onto the org's board.

        Args:
            state: Litestar app state carrying the wired board service.
            project: Optional project filter; omit to span every project.

        Returns:
            ``ApiResponse[KanbanBoardView]`` with per-column cards + WIP state.
        """
        app_state: AppState = state.app_state
        view = await kanban_board_service_of(app_state).board_snapshot(project=project)
        return ApiResponse(data=view)

    @post(
        "/move",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("board.move", key="user"),
        ],
    )
    async def move_card(
        self,
        state: State,
        data: BoardMovePayload,
    ) -> ApiResponse[Task]:
        """Move a card to another column, validating the move + WIP.

        Args:
            state: Litestar app state carrying the wired board service.
            data: The card id and target column.

        Returns:
            ``ApiResponse[Task]`` with the card after the move.
        """
        app_state: AppState = state.app_state
        task = await kanban_board_service_of(app_state).move_task(
            data.task_id,
            data.target_column,
            requested_by=_extract_requester(state),
        )
        return ApiResponse(data=task)
