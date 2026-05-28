"""Task controller: CRUD + board entry into the live pipeline spine."""

import asyncio
from typing import Annotated, Final

from litestar import Controller, delete, get, patch, post
from litestar.datastructures import State
from litestar.params import QueryParameter
from litestar.status_codes import (
    HTTP_200_OK,
    HTTP_202_ACCEPTED,
    HTTP_204_NO_CONTENT,
)

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
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.responses import require_resource_or_404
from synthorg.api.state import AppState
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.client.state import client_simulation_state_of
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import AgentRuntimeNotConfiguredError
from synthorg.core.enums import TaskStatus
from synthorg.core.task import Task
from synthorg.engine.errors import TaskNotFoundError
from synthorg.engine.pipeline.entry.task_board_adapter import (
    TaskBoardEntryAdapter,
    TaskBoardFiling,
)
from synthorg.engine.pipeline.errors import WorkIntakeRejectedError
from synthorg.engine.pipeline.models import WorkSource
from synthorg.engine.state import (
    EngineStateSlice,
    task_engine_of,
)
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.background_tasks import log_task_exceptions
from synthorg.observability.events.api import (
    API_AUTH_FALLBACK,
    API_RESOURCE_NOT_FOUND,
    API_TASK_BOARD_PIPELINE_FAILED,
    API_TASK_BOARD_REJECTED_NO_ADAPTER,
    API_TASK_BOARD_SUBMITTED,
    API_TASK_CANCELLED,
    API_TASK_CREATED_BY_MISMATCH,
    API_TASK_DELETED,
    API_TASK_UPDATED,
)
from synthorg.observability.events.task import TASK_STATUS_CHANGED
from synthorg.workers.state import worker_execution_service_of

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


def _extract_requester(state: State) -> str:
    """Extract requester identity from the authenticated user.

    Falls back to ``"api"`` when the connection carries no user
    (e.g. in tests without auth middleware). Logs a warning on
    fallback so auth misconfiguration is visible in production.

    Returns:
        Resulting string.
    """
    user = getattr(state, "_connection_user", None)
    if user is not None and hasattr(user, "user_id"):
        return str(user.user_id)
    logger.warning(
        API_AUTH_FALLBACK,
        note="No authenticated user found, falling back to 'api'",
    )
    return "api"


async def process_task_board_pipeline(
    *,
    adapter: TaskBoardEntryAdapter,
    filing: TaskBoardFiling,
) -> None:
    """Drive a board filing through the work pipeline spine.

    Runs in a detached background task; the HTTP handler already
    returned ``202``. Failures are logged at WARNING (intake-rejection)
    or ERROR (any other pipeline failure) keyed by ``correlation_id``;
    ``MemoryError`` and ``RecursionError`` propagate. ``CancelledError``
    propagates so app shutdown does not convert a cancellation into a
    spurious error log.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    try:
        await adapter.submit(filing)
    except asyncio.CancelledError:
        raise
    except WorkIntakeRejectedError as exc:
        # Intake declining the work is a normal outcome, not a defect.
        logger.warning(
            API_TASK_BOARD_PIPELINE_FAILED,
            correlation_id=filing.correlation_id,
            project=filing.project,
            outcome="intake_rejected",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    except Exception as exc:
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            API_TASK_BOARD_PIPELINE_FAILED,
            exc,
            correlation_id=filing.correlation_id,
            project=filing.project,
            outcome="pipeline_error",
        )


def _spawn_task_board_pipeline(
    *,
    sim_state: ClientSimulationState,
    adapter: TaskBoardEntryAdapter,
    filing: TaskBoardFiling,
) -> None:
    """Spawn + track the background board-pipeline run.

    A detached task (not a ``TaskGroup``) is correct here: the create
    handler returns ``202`` immediately and the pipeline run outlives
    that scope by design. Lifecycle mirrors ``_spawn_intake_pipeline``
    in ``controllers/requests.py``: a strong reference in
    ``sim_state.background_tasks`` keeps the task from being GC'd
    mid-flight, the exception logger is attached before the
    set-discard so a fast-completing failure still surfaces, and the
    reference is added synchronously here (no ``await`` between
    ``create_task`` and ``add``).
    """
    task = asyncio.create_task(
        process_task_board_pipeline(adapter=adapter, filing=filing),
    )
    task.add_done_callback(
        log_task_exceptions(
            logger,
            API_TASK_BOARD_PIPELINE_FAILED,
            correlation_id=filing.correlation_id,
        ),
    )
    task.add_done_callback(sim_state.background_tasks.discard)
    sim_state.background_tasks.add(task)


class TaskController(Controller):
    """Full CRUD for tasks via ``TaskEngine`` plus board-entry POST."""

    path = "/tasks"
    tags = ("tasks",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_tasks(  # noqa: PLR0913
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
    ) -> PaginatedResponse[Task]:
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
        return PaginatedResponse(data=page, pagination=meta)

    @get("/{task_id:str}")
    async def get_task(
        self,
        state: State,
        task_id: PathId,
    ) -> ApiResponse[Task]:
        """Get a task by ID.

        Args:
            state: Application state.
            task_id: Task identifier.

        Returns:
            Task envelope.

        Raises:
            NotFoundError: If the task is not found.
        """
        app_state: AppState = state.app_state
        task_engine = task_engine_of(app_state)
        task = await task_engine.get_task(task_id)
        task = require_resource_or_404(
            task,
            resource_type="task",
            identifier=task_id,
            log_event=API_RESOURCE_NOT_FOUND,
            operation="read",
            error_class=TaskNotFoundError,
        )
        return ApiResponse(data=task)

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
        requester = _extract_requester(state)
        # Read the adapter once and reuse the same instance for the
        # presence check and the spawn; otherwise a concurrent unwire/
        # rewire between the check and the second ``*_of(app_state)``
        # lookup could bypass the rejection path or surface an
        # unexpected ``ServiceUnavailableError``.
        adapter = app_state.slice(EngineStateSlice).task_board_entry_adapter
        if adapter is None:
            logger.warning(
                API_TASK_BOARD_REJECTED_NO_ADAPTER,
                title=data.title,
                requester=requester,
                project=data.project,
            )
            raise AgentRuntimeNotConfiguredError
        if data.created_by != requester:
            logger.warning(
                API_TASK_CREATED_BY_MISMATCH,
                note="created_by differs from authenticated requester",
                created_by=data.created_by,
                requester=requester,
            )
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
        _spawn_task_board_pipeline(
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
    ) -> ApiResponse[Task]:
        """Update task fields.

        Args:
            state: Application state.
            task_id: Task identifier.
            data: Fields to update.

        Returns:
            Updated task envelope.

        Raises:
            NotFoundError: If the task is not found.
        """
        app_state: AppState = state.app_state
        updates = data.model_dump(
            exclude_none=True,
            exclude={"expected_version"},
        )
        task_engine = task_engine_of(app_state)
        task = await task_engine.update_task(
            task_id,
            updates,
            requested_by=_extract_requester(state),
            expected_version=data.expected_version,
        )
        logger.info(API_TASK_UPDATED, task_id=task_id, fields=list(updates))
        return ApiResponse(data=task)

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
    ) -> ApiResponse[Task]:
        """Perform a status transition on a task.

        Pure ``TaskEngine`` status walk; the spine-created task moves
        through the board columns by transitioning. The pipeline spine
        owns its own intra-task transitions during execution, so the
        board's transitions are display-only after the spine has
        started (the WS ``task.status_changed`` events keep both in
        sync).

        Returns:
            ``ApiResponse[Task]`` instance.
        """
        app_state: AppState = state.app_state
        requester = _extract_requester(state)
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
        return ApiResponse(data=task)

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
        """Delete a task.

        Args:
            state: Application state.
            task_id: Task identifier.

        Raises:
            NotFoundError: If the task is not found.
        """
        app_state: AppState = state.app_state
        task_engine = task_engine_of(app_state)
        await task_engine.delete_task(
            task_id,
            requested_by=_extract_requester(state),
        )
        logger.info(API_TASK_DELETED, task_id=task_id)

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
    ) -> ApiResponse[Task]:
        """Execute one step of a task on behalf of a worker.

        Called by the distributed worker (``synthorg.workers.executor``)
        when a JetStream claim arrives. Delegates to
        ``WorkerExecutionService.execute_once`` so the agent-runtime
        invocation is configurable per deployment.

        Returns:
            ``ApiResponse[Task]`` instance.
        """
        app_state: AppState = state.app_state
        requester = _extract_requester(state)
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
        return ApiResponse(data=task)

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
    ) -> ApiResponse[Task]:
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
            requested_by=_extract_requester(state),
            reason=data.reason,
        )
        logger.info(API_TASK_CANCELLED, task_id=task_id)
        return ApiResponse(data=task)
