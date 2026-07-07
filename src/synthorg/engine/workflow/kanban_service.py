# module-kind: service
"""Kanban board service: project tasks onto columns and move them.

Renders the org's board as a projection of task status (``STATUS_TO_COLUMN``)
and drives human/dashboard column moves through the task engine, validating
the column transition and enforcing WIP limits. WIP limits + enforcement are
read per request from the settings resolver (``engine.kanban_*``), so a
runtime change applies to the next board operation with no restart.
"""

from synthorg.core.task import Task
from synthorg.engine.errors import (
    KanbanInvalidMoveError,
    KanbanWipLimitError,
    TaskNotFoundError,
)
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.workflow.enums import WorkflowType
from synthorg.engine.workflow.kanban_board import (
    KanbanConfig,
    KanbanWipLimit,
    check_wip_limit,
)
from synthorg.engine.workflow.kanban_columns import (
    COLUMN_TO_STATUSES,
    STATUS_TO_COLUMN,
    KanbanColumn,
    resolve_task_transitions,
    validate_column_transition,
)
from synthorg.engine.workflow.kanban_view import (
    BOARD_COLUMN_ORDER,
    KanbanBoardView,
    KanbanColumnView,
)
from synthorg.persistence.task_protocol import TaskFilterSpec, TaskRepository
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

_SETTINGS_NS = "engine"
_LIMIT_KEYS: dict[KanbanColumn, str] = {
    KanbanColumn.IN_PROGRESS: "kanban_wip_in_progress",
    KanbanColumn.REVIEW: "kanban_wip_review",
}


class KanbanBoardService:
    """Read + move surface for the per-org Kanban board.

    Structurally board-only: the board never bypasses the task engine, so
    every move flows through the single-writer status machine (and its audit
    trail). WIP enforcement gates human moves only; agent execution driven by
    the coordinator is not throttled here.
    """

    __slots__ = ("_config_resolver", "_task_engine", "_tasks")

    def __init__(
        self,
        *,
        task_repository: TaskRepository,
        task_engine: TaskEngine,
        config_resolver: ConfigResolverProtocol,
    ) -> None:
        self._tasks = task_repository
        self._task_engine = task_engine
        self._config_resolver = config_resolver

    async def _resolve_limits(self) -> dict[KanbanColumn, int]:
        """Read the per-column WIP limits from settings (hot).

        Returns:
            Mapping of the flow-limited columns to their current limit.
        """
        return {
            column: await self._config_resolver.get_int(_SETTINGS_NS, key)
            for column, key in _LIMIT_KEYS.items()
        }

    async def _resolve_kanban_config(
        self, limits: dict[KanbanColumn, int]
    ) -> KanbanConfig:
        """Build a :class:`KanbanConfig` from the current settings.

        Returns:
            A config carrying the resolved per-column limits + enforcement
            posture.
        """
        enforce = await self._config_resolver.get_bool(
            _SETTINGS_NS, "kanban_enforce_wip"
        )
        return KanbanConfig(
            wip_limits=tuple(
                KanbanWipLimit(column=column, limit=limit)
                for column, limit in limits.items()
            ),
            enforce_wip=enforce,
        )

    async def board_snapshot(self, *, project: str | None = None) -> KanbanBoardView:
        """Project current tasks onto the board's columns.

        Args:
            project: Optional project filter; ``None`` spans every project.

        Returns:
            The full board with per-column cards, counts, and WIP state.
        """
        limits = await self._resolve_limits()
        enforce = await self._config_resolver.get_bool(
            _SETTINGS_NS, "kanban_enforce_wip"
        )
        workflow_type = await self._config_resolver.get_enum(
            _SETTINGS_NS, "workflow_type", WorkflowType
        )
        columns: list[KanbanColumnView] = []
        for column in BOARD_COLUMN_ORDER:
            tasks = await self._column_tasks(column, project=project)
            limit = limits.get(column)
            count = len(tasks)
            columns.append(
                KanbanColumnView(
                    column=column,
                    tasks=tasks,
                    count=count,
                    limit=limit,
                    over_limit=limit is not None and count > limit,
                )
            )
        return KanbanBoardView(
            columns=tuple(columns),
            workflow_type=workflow_type,
            enforce_wip=enforce,
        )

    async def _column_tasks(
        self, column: KanbanColumn, *, project: str | None
    ) -> tuple[Task, ...]:
        """Fetch every task whose status maps to *column*.

        Returns:
            The column's cards (a column can span more than one status).
        """
        cards: list[Task] = []
        for status in COLUMN_TO_STATUSES[column]:
            cards.extend(
                await self._tasks.query(TaskFilterSpec(status=status, project=project))
            )
        return tuple(cards)

    async def move_task(
        self,
        task_id: str,
        target_column: KanbanColumn,
        *,
        requested_by: str,
    ) -> Task:
        """Move a card to *target_column*, validating the transition + WIP.

        Args:
            task_id: The card's task id.
            target_column: The column to move it into.
            requested_by: Identity performing the move (audit).

        Returns:
            The task after walking the resolved status path.

        Raises:
            TaskNotFoundError: When the task does not exist.
            KanbanInvalidMoveError: When the card is off-board or the column
                transition is not legal.
            KanbanWipLimitError: When enforcement is on and the target column
                is at capacity.
        """
        task = await self._task_engine.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        current = STATUS_TO_COLUMN[task.status]
        if current is None:
            msg = f"Task {task_id} is off-board (status {task.status.value})"
            raise KanbanInvalidMoveError(msg)
        if current is target_column:
            return task
        try:
            validate_column_transition(current, target_column)
        except ValueError as exc:
            raise KanbanInvalidMoveError(str(exc)) from exc

        limits = await self._resolve_limits()
        kanban = await self._resolve_kanban_config(limits)
        target_count = len(await self._column_tasks(target_column, project=None))
        wip = check_wip_limit(kanban, target_column, {target_column: target_count})
        if not wip.allowed:
            msg = (
                f"Column {target_column.value!r} is at its WIP limit "
                f"({wip.limit}); move rejected"
            )
            raise KanbanWipLimitError(msg)

        updated = task
        for status in resolve_task_transitions(current, target_column):
            updated, _ = await self._task_engine.transition_task(
                task_id,
                status,
                requested_by=requested_by,
                reason=f"kanban board move to {target_column.value}",
            )
        return updated
