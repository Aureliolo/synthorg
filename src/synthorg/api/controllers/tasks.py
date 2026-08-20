"""Task controller: CRUD + board entry into the live pipeline spine."""

from typing import Annotated, Final

from litestar import Controller, delete, get, patch, post
from litestar.datastructures import State
from litestar.params import QueryParameter
from litestar.status_codes import (
    HTTP_200_OK,
    HTTP_202_ACCEPTED,
    HTTP_204_NO_CONTENT,
)

from synthorg.api.controllers._bulk_delete import BulkDeleteRequest, BulkDeleteResult
from synthorg.api.controllers._deletion_record import deleted_task_error
from synthorg.api.controllers._requester import extract_requester
from synthorg.api.controllers._task_board_pipeline import spawn_task_board_pipeline
from synthorg.api.controllers._task_money_ceiling import guard_task_money_ceiling
from synthorg.api.controllers._task_names import names_and_titles
from synthorg.api.controllers._task_removal import remove_task, remove_tasks
from synthorg.api.dto import (
    ApiResponse,
    CancelTaskRequest,
    CreateTaskRequest,
    ExecuteTaskRequest,
    PaginatedResponse,
    TaskBoardSubmissionResponse,
    TransitionTaskRequest,
    UpdateTaskRequest,
)
from synthorg.api.dto_named_rows import TaskRow, task_rows
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.client.state import client_simulation_state_of
from synthorg.core.domain_errors import (
    AgentRuntimeNotConfiguredError,
)
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.pipeline.entry.task_board_adapter import (
    TaskBoardFiling,
)
from synthorg.engine.pipeline.models import WorkSource
from synthorg.engine.state import (
    EngineStateSlice,
    task_engine_of,
)
from synthorg.observability import (
    get_logger,
)
from synthorg.observability.events.api import (
    API_TASK_BOARD_REJECTED_NO_ADAPTER,
    API_TASK_BOARD_SUBMITTED,
    API_TASK_CANCELLED,
    API_TASK_DELETED,
    API_TASK_LISTED,
    API_TASK_UPDATED,
)
from synthorg.observability.events.task import TASK_STATUS_CHANGED
from synthorg.workers.state import worker_execution_service_of

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


async def _named(app_state: AppState, task: Task) -> TaskRow:
    """Pair a task's references with the names the operator knows them by.

    Its dependencies are titled here too: the detail surface lists them, and a
    list of ids names nothing an operator can act on.

    Returns:
        The task as the dashboard reads it.
    """
    names, titles = await names_and_titles(app_state, task.dependencies)
    return TaskRow.of(task, names, titles)


class TaskController(Controller):
    """Full CRUD for tasks via ``TaskEngine`` plus board-entry POST."""

    path = "/tasks"
    tags = ("tasks",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_tasks(
        self,
        state: State,
        status: Annotated[
            TaskStatus | None,
            QueryParameter(description="Filter to tasks in this status."),
        ] = None,
        assigned_to: Annotated[
            str | None,
            QueryParameter(
                max_length=256,
                description="Filter to tasks assigned to this agent.",
            ),
        ] = None,
        project: Annotated[
            str | None,
            QueryParameter(
                max_length=256,
                description="Filter to tasks scoped to this project.",
            ),
        ] = None,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[TaskRow]:
        """List tasks with optional filters.

        Args:
            state: Application state.
            status: Filter by status.
            assigned_to: Filter by assignee.
            project: Filter by project.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated task list.
        """
        app_state: AppState = state.app_state
        task_engine = task_engine_of(app_state)
        tasks, total = await task_engine.list_tasks(
            status=status,
            assigned_to=assigned_to,
            project=project,
        )
        page, meta = paginate_cursor(
            tasks,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        meta = meta.model_copy(update={"total": total})
        logger.debug(API_TASK_LISTED, count=len(page), total=total)
        # Once for the whole page, not once per row. Titled here as well as on
        # the detail read because the field promises the same thing on both, and
        # a row answering "nothing could name this" on the list while the detail
        # named it would be making that claim falsely.
        names, titles = await names_and_titles(
            app_state,
            [dependency for task in page for dependency in task.dependencies],
        )
        return PaginatedResponse(data=task_rows(page, names, titles), pagination=meta)

    @get("/{task_id:str}")
    async def get_task(
        self,
        state: State,
        task_id: PathId,
    ) -> ApiResponse[TaskRow]:
        """Get a task by ID.

        Args:
            state: Application state.
            task_id: Task identifier.

        Returns:
            Task envelope.

        Raises:
            TaskNotFoundError: If the task is not found. When a tombstone
                answers for the id, the message says what the task was and
                who removed it: this route is what the surviving cost,
                metric and decision rows resolve their ``task_id`` through,
                and "not found" alone is the dangling reference dropping
                the foreign keys would otherwise have created.
        """
        app_state: AppState = state.app_state
        task_engine = task_engine_of(app_state)
        task = await task_engine.get_task(task_id)
        if task is None:
            raise await deleted_task_error(app_state, task_id)
        return ApiResponse(data=await _named(app_state, task))

    @post(
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("tasks.create", key="user"),
        ],
        status_code=HTTP_202_ACCEPTED,
    )
    async def create_task(
        self,
        state: State,
        data: CreateTaskRequest,
    ) -> ApiResponse[TaskBoardSubmissionResponse]:
        """File a new task from the board into the live work pipeline.

        The board does not pre-create a task. Filing routes through the
        :class:`TaskBoardEntryAdapter` which builds a
        :class:`~synthorg.engine.pipeline.models.WorkItem` with
        ``source=TASK_BOARD`` and drives the pipeline spine in a
        detached background coroutine. The spine creates the task
        inside its intake phase; the board UI subscribes to the
        ``tasks`` WS channel and inserts the spine-created task on the
        ``task.created`` event correlated by ``correlation_id``.

        Returns:
            HTTP 202 Accepted with a :class:`TaskBoardSubmissionResponse`.

        Raises:
            AgentRuntimeNotConfiguredError: When no board entry adapter
                is wired (empty company / no provider). The
                ``AGENT_RUNTIME_NOT_CONFIGURED`` error code makes the
                "needs a provider" signal explicit.
        """
        app_state: AppState = state.app_state
        requester = extract_requester()
        # Read the adapter once and reuse the same instance for the
        # presence check and the spawn; otherwise a concurrent unwire/
        # rewire between the check and the second ``*_of(app_state)``
        # lookup could bypass the rejection path or surface an
        # unexpected ``ServiceUnavailableError``.
        adapter = app_state.slice(EngineStateSlice).task_board_entry_adapter
        if adapter is None:
            # The title is human-typed and says nothing about why the board
            # refused; the project and requester bound the refusal without
            # putting free text a person wrote into the log.
            logger.warning(
                API_TASK_BOARD_REJECTED_NO_ADAPTER,
                requester=requester,
                project=data.project,
            )
            raise AgentRuntimeNotConfiguredError
        filing = TaskBoardFiling(
            title=data.title,
            description=data.description,
            task_type=data.type,
            priority=data.priority,
            project=data.project,
            requested_by=requester,
            estimated_complexity=data.estimated_complexity,
        )
        sim_state = client_simulation_state_of(app_state)
        spawn_task_board_pipeline(
            sim_state=sim_state,
            adapter=adapter,
            filing=filing,
        )
        logger.info(
            API_TASK_BOARD_SUBMITTED,
            correlation_id=filing.correlation_id,
            project=filing.project,
            task_type=filing.task_type.value,
            source=WorkSource.TASK_BOARD.value,
        )
        return ApiResponse(
            data=TaskBoardSubmissionResponse(
                correlation_id=filing.correlation_id,
                title=filing.title,
                project=filing.project,
            ),
        )

    @patch(
        "/{task_id:str}",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("tasks.update", key="user"),
        ],
    )
    async def update_task(
        self,
        state: State,
        task_id: PathId,
        data: UpdateTaskRequest,
    ) -> ApiResponse[TaskRow]:
        """Update task fields.

        Args:
            state: Application state.
            task_id: Task identifier.
            data: Fields to update.

        Returns:
            Updated task envelope.

        Raises:
            NotFoundError: If the task is not found.
            ValidationError: If the money ceiling could never bind.
        """
        app_state: AppState = state.app_state
        updates = data.model_dump(
            exclude_none=True,
            exclude={"expected_version"},
        )
        await guard_task_money_ceiling(app_state, updates, task_id=task_id)
        task_engine = task_engine_of(app_state)
        task = await task_engine.update_task(
            task_id,
            updates,
            requested_by=extract_requester(),
            expected_version=data.expected_version,
        )
        logger.info(API_TASK_UPDATED, task_id=task_id, fields=list(updates))
        return ApiResponse(data=await _named(app_state, task))

    @post(
        "/{task_id:str}/transition",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("tasks.transition", key="user"),
        ],
    )
    async def transition_task(
        self,
        state: State,
        task_id: PathId,
        data: TransitionTaskRequest,
    ) -> ApiResponse[TaskRow]:
        """Perform a status transition on a task.

        Pure ``TaskEngine`` status walk; the spine-created task moves
        through the board columns by transitioning. The pipeline spine
        owns its own intra-task transitions during execution, so the
        board's transitions are display-only after the spine has
        started (the WS ``task.status_changed`` events keep both in
        sync).

        Returns:
            ``ApiResponse[TaskRow]`` instance.
        """
        app_state: AppState = state.app_state
        requester = extract_requester()
        overrides: dict[str, object] = {}
        if data.assigned_to is not None:
            overrides["assigned_to"] = data.assigned_to
        task_engine = task_engine_of(app_state)
        task, from_status = await task_engine.transition_task(
            task_id,
            data.target_status,
            requested_by=requester,
            reason=f"API transition to {data.target_status.value}",
            expected_version=data.expected_version,
            **overrides,
        )
        logger.info(
            TASK_STATUS_CHANGED,
            task_id=task_id,
            from_status=from_status.value if from_status else None,
            to_status=task.status.value,
        )
        return ApiResponse(data=await _named(app_state, task))

    @delete(
        "/{task_id:str}",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("tasks.delete", key="user"),
        ],
        status_code=HTTP_204_NO_CONTENT,
    )
    async def delete_task(
        self,
        state: State,
        task_id: PathId,
    ) -> None:
        """Delete a task, unless a plan still names it as its objective.

        The refusal is the engine's, not this route's: three callers
        reach ``delete_task`` and the guard belongs on the path all
        three take.

        Args:
            state: Application state.
            task_id: Task identifier.

        Raises:
            NotFoundError: If the task is not found.
            PlanParentTaskInUseError: If a plan still references this task.
            ConflictError: An approval about this task was decided while the
                delete was being prepared, so it is still being acted on.
                Nothing is removed and the operator retries.
        """
        app_state: AppState = state.app_state
        # First, before anything is written: an unbound requester is a server
        # fault (the auth middleware owns the 401 on this route), and a fault
        # must not first expire a task's approvals and then refuse the delete.
        requested_by = extract_requester()
        await remove_task(app_state, task_id, requested_by=requested_by)
        logger.info(API_TASK_DELETED, task_id=task_id)

    @post(
        "/bulk-delete",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("tasks.bulk_delete", key="user"),
        ],
    )
    async def bulk_delete_tasks(
        self,
        state: State,
        data: BulkDeleteRequest,
    ) -> ApiResponse[BulkDeleteResult]:
        """Delete every selected task, reporting each row's outcome.

        A task a plan still names as its objective refuses, and clearing a
        board is exactly the selection that mixes those in, so each refusal is
        collected against its own row.

        Returns:
            What was removed and what remains.
        """
        result = await remove_tasks(
            state.app_state,
            data.ids,
            requested_by=extract_requester(),
        )
        return ApiResponse(data=result)

    @post(
        "/{task_id:str}/execute",
        # The endpoint mutates the existing task, not creates a new
        # resource. Override Litestar's default 201 with 200 so the
        # worker's ACK/NACK contract reads the success class directly
        # instead of treating "Created" as a special case.
        status_code=HTTP_200_OK,
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("tasks.execute", key="user"),
        ],
    )
    async def execute_task(
        self,
        state: State,
        task_id: PathId,
        data: ExecuteTaskRequest,
    ) -> ApiResponse[TaskRow]:
        """Execute one step of a task on behalf of a worker.

        Worker-internal endpoint: there is no dashboard UI for it by
        design. It is called only by the distributed worker
        (``synthorg.workers.executor``) when a JetStream claim arrives,
        as part of the worker-to-API execution contract. Delegates to
        ``WorkerExecutionService.execute_once`` so the agent-runtime
        invocation is configurable per deployment.

        Returns:
            ``ApiResponse[TaskRow]`` instance.
        """
        app_state: AppState = state.app_state
        requester = extract_requester()
        task = await worker_execution_service_of(app_state).execute_once(
            task_id=task_id,
            previous_status=data.previous_status,
            new_status=data.new_status,
            idempotency_key=data.idempotency_key,
            requested_by=requester,
        )
        logger.info(
            TASK_STATUS_CHANGED,
            task_id=task_id,
            from_status=data.previous_status,
            to_status=task.status.value,
            triggered_by="worker_executor",
        )
        return ApiResponse(data=await _named(app_state, task))

    @post(
        "/{task_id:str}/cancel",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("tasks.cancel", key="user"),
        ],
    )
    async def cancel_task(
        self,
        state: State,
        task_id: PathId,
        data: CancelTaskRequest,
    ) -> ApiResponse[TaskRow]:
        """Cancel a task.

        Args:
            state: Application state.
            task_id: Task identifier.
            data: Cancellation payload with reason.

        Returns:
            Cancelled task envelope.

        Raises:
            NotFoundError: If the task is not found.
        """
        app_state: AppState = state.app_state
        task_engine = task_engine_of(app_state)
        task, _prior_status = await task_engine.cancel_task(
            task_id,
            requested_by=extract_requester(),
            reason=data.reason,
        )
        logger.info(API_TASK_CANCELLED, task_id=task_id)
        return ApiResponse(data=await _named(app_state, task))
