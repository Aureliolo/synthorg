"""Task controller -- full CRUD via TaskEngine."""

from typing import Annotated, Final

from litestar import Controller, delete, get, patch, post
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter
from litestar.status_codes import HTTP_204_NO_CONTENT

from synthorg.api.dto import (
    ApiResponse,
    CancelTaskRequest,
    CreateTaskRequest,
    ExecuteTaskRequest,
    PaginatedResponse,
    TransitionTaskRequest,
    UpdateTaskRequest,
)
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import CursorLimit, CursorParam, paginate_cursor
from synthorg.api.path_params import PathId  # noqa: TC001
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.responses import require_resource_or_404
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.enums import TaskStatus  # noqa: TC001
from synthorg.core.error_taxonomy import ErrorCode
from synthorg.core.task import Task  # noqa: TC001
from synthorg.engine.task_engine_models import CreateTaskData
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_AUTH_FALLBACK,
    API_RESOURCE_NOT_FOUND,
    API_TASK_CANCELLED,
    API_TASK_CREATED_BY_MISMATCH,
    API_TASK_DELETED,
    API_TASK_UPDATED,
)
from synthorg.observability.events.task import (
    TASK_CREATED,
    TASK_STATUS_CHANGED,
)

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


def _extract_requester(state: State) -> str:
    """Extract requester identity from the authenticated user.

    Falls back to ``"api"`` when the connection carries no user
    (e.g. in tests without auth middleware).  Logs a warning on
    fallback so auth misconfiguration is visible in production.
    """
    user = getattr(state, "_connection_user", None)
    if user is not None and hasattr(user, "user_id"):
        return str(user.user_id)
    logger.warning(
        API_AUTH_FALLBACK,
        note="No authenticated user found, falling back to 'api'",
    )
    return "api"


class TaskController(Controller):
    """Full CRUD for tasks via ``TaskEngine``."""

    path = "/tasks"
    tags = ("tasks",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_tasks(  # noqa: PLR0913
        self,
        state: State,
        status: Annotated[
            TaskStatus | None,
            Parameter(description="Filter to tasks in this status."),
        ] = None,
        assigned_to: Annotated[
            str | None,
            Parameter(
                max_length=256,
                description="Filter to tasks assigned to this agent.",
            ),
        ] = None,
        project: Annotated[
            str | None,
            Parameter(
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
        tasks, total = await app_state.task_engine.list_tasks(
            status=status,
            assigned_to=assigned_to,
            project=project,
        )
        page, meta = paginate_cursor(
            tasks,
            limit=limit,
            cursor=cursor,
            secret=app_state.cursor_secret,
        )
        # ``total`` is still reported so callers that rely on a real
        # count for progress indicators keep working; pagination goes
        # via the opaque cursor.
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
        task = await app_state.task_engine.get_task(task_id)
        task = require_resource_or_404(
            task,
            resource_type="task",
            identifier=task_id,
            log_event=API_RESOURCE_NOT_FOUND,
            operation="read",
            code=ErrorCode.TASK_NOT_FOUND,
        )
        return ApiResponse(data=task)

    @post(
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("tasks.create", key="user"),
        ],
        status_code=201,
    )
    async def create_task(
        self,
        state: State,
        data: CreateTaskRequest,
    ) -> ApiResponse[Task]:
        """Create a new task.

        Args:
            state: Application state.
            data: Task creation payload.

        Returns:
            Created task envelope.
        """
        app_state: AppState = state.app_state
        requester = _extract_requester(state)
        task_data = CreateTaskData(
            title=data.title,
            description=data.description,
            type=data.type,
            priority=data.priority,
            project=data.project,
            created_by=data.created_by,
            assigned_to=data.assigned_to,
            estimated_complexity=data.estimated_complexity,
            budget_limit=data.budget_limit,
        )
        if data.created_by != requester:
            logger.warning(
                API_TASK_CREATED_BY_MISMATCH,
                note="created_by differs from authenticated requester",
                created_by=data.created_by,
                requester=requester,
            )
        task = await app_state.task_engine.create_task(
            task_data,
            requested_by=requester,
        )
        logger.info(
            TASK_CREATED,
            task_id=task.id,
            title=task.title,
        )
        return ApiResponse(data=task)

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
        task = await app_state.task_engine.update_task(
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

        Args:
            state: Application state.
            task_id: Task identifier.
            data: Transition payload.

        Returns:
            Transitioned task envelope.

        Raises:
            NotFoundError: If the task is not found.
        """
        app_state: AppState = state.app_state
        requester = _extract_requester(state)
        overrides: dict[str, object] = {}
        if data.assigned_to is not None:
            overrides["assigned_to"] = data.assigned_to
        task, from_status = await app_state.task_engine.transition_task(
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
        await app_state.task_engine.delete_task(
            task_id,
            requested_by=_extract_requester(state),
        )
        logger.info(API_TASK_DELETED, task_id=task_id)

    @post(
        "/{task_id:str}/execute",
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
        when a JetStream claim arrives. The endpoint delegates to
        ``WorkerExecutionService.execute_once`` so the agent-runtime
        invocation is configurable per deployment; the controller
        itself only routes auth + HTTP envelope.

        The response carries the task at its post-execution status so
        the worker can map the outcome (terminal status -> ACK,
        non-terminal -> NACK / RETRY).
        """
        app_state: AppState = state.app_state
        requester = _extract_requester(state)
        task = await app_state.worker_execution_service.execute_once(
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
        task, _prior_status = await app_state.task_engine.cancel_task(
            task_id,
            requested_by=_extract_requester(state),
            reason=data.reason,
        )
        logger.info(API_TASK_CANCELLED, task_id=task_id)
        return ApiResponse(data=task)
