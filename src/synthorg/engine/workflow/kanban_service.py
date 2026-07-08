# module-kind: service
"""Kanban board service: project tasks onto columns and move them.

Renders the org's board as a projection of task status (``STATUS_TO_COLUMN``)
and drives human/dashboard column moves through the task engine, validating
the column transition and enforcing WIP limits. WIP limits + enforcement are
read per request from the settings resolver (``engine.kanban_*``), so a
runtime change applies to the next board operation with no restart.
"""

import asyncio

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.engine.errors import (
    KanbanInvalidMoveError,
    KanbanWipLimitError,
    SprintTaskNotInBacklogError,
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
from synthorg.engine.workflow.sprint_service import SprintService
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.workflow import (
    SPRINT_GATE_BLOCKED,
    SPRINT_GATE_CHECK_FAILED,
)
from synthorg.persistence.task_protocol import TaskFilterSpec, TaskRepository
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

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

    __slots__ = (
        "_config_resolver",
        "_move_lock",
        "_sprint_service",
        "_task_engine",
        "_tasks",
    )

    def __init__(
        self,
        *,
        task_repository: TaskRepository,
        task_engine: TaskEngine,
        config_resolver: ConfigResolverProtocol,
        sprint_service: SprintService | None = None,
    ) -> None:
        self._tasks = task_repository
        self._task_engine = task_engine
        self._config_resolver = config_resolver
        # Advisory sprint gate: when wired, a move into In-Progress is
        # rejected for a task outside the active sprint backlog. Optional so
        # non-agile boards and pre-sprint-wiring boots keep working.
        self._sprint_service = sprint_service
        # Serialises the read-check-move critical section in ``move_task`` so
        # two concurrent moves cannot both pass a stale WIP check before either
        # transition lands (a TOCTOU that would let a column exceed its limit).
        self._move_lock = asyncio.Lock()

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
        # Per-column lookups are independent; fan them out concurrently.
        async with asyncio.TaskGroup() as group:
            column_cards = {
                column: group.create_task(self._column_tasks(column, project=project))
                for column in BOARD_COLUMN_ORDER
            }
        columns = tuple(
            KanbanColumnView(
                column=column,
                tasks=column_cards[column].result(),
                limit=limits.get(column),
            )
            for column in BOARD_COLUMN_ORDER
        )
        return KanbanBoardView(
            columns=columns,
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

    async def _enforce_sprint_gate(
        self, task: Task, target_column: KanbanColumn
    ) -> None:
        """Reject a move into flow for a task outside the active sprint.

        Advisory: a no-op unless a ``SprintService`` is wired and the move
        targets the In-Progress column. The service short-circuits to
        "workable" when sprints are disabled, the workflow is not
        ``agile_kanban``, or the project has no open sprint. If the check
        itself fails (e.g. sprint-store outage), the move is allowed
        through so the gate stays advisory rather than blocking the board.

        Raises:
            SprintTaskNotInBacklogError: When the gate is active and the
                task is not in the open sprint backlog.
        """
        if (
            self._sprint_service is None
            or target_column is not KanbanColumn.IN_PROGRESS
        ):
            return
        try:
            workable = await self._sprint_service.is_task_workable(
                str(task.id), task.project
            )
        except Exception as exc:  # noqa: BLE001 -- advisory gate: fail open
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                SPRINT_GATE_CHECK_FAILED,
                exc,
                task_id=str(task.id),
                project=task.project,
            )
            return
        if workable:
            return
        logger.warning(
            SPRINT_GATE_BLOCKED,
            task_id=str(task.id),
            project=task.project,
            target_column=target_column.value,
        )
        msg = (
            f"Task {task.id} is not in the active sprint backlog; "
            "pull it into the sprint before moving it into progress"
        )
        raise SprintTaskNotInBacklogError(msg)

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

        await self._enforce_sprint_gate(task, target_column)

        limits = await self._resolve_limits()
        kanban = await self._resolve_kanban_config(limits)
        # Hold the move lock across the WIP read + transition so the target
        # count a move validates against is the count it then mutates: two
        # concurrent moves are serialised rather than both passing a stale
        # snapshot and pushing the column over its limit.
        async with self._move_lock:
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
