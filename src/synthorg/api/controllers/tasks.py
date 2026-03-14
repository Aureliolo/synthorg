"""Task controller — full CRUD via TaskEngine."""

from litestar import Controller, delete, get, patch, post
from litestar.datastructures import State  # noqa: TC002

from synthorg.api.dto import (
    ApiResponse,
    CancelTaskRequest,
    CreateTaskRequest,
    PaginatedResponse,
    TransitionTaskRequest,
    UpdateTaskRequest,
)
from synthorg.api.errors import (
    ApiValidationError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import PaginationLimit, PaginationOffset, paginate
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.enums import TaskStatus  # noqa: TC001
from synthorg.core.task import Task  # noqa: TC001
from synthorg.engine.errors import (
    TaskEngineNotRunningError,
    TaskEngineQueueFullError,
    TaskInternalError,
    TaskMutationError,
    TaskNotFoundError,
    TaskVersionConflictError,
)
from synthorg.engine.task_engine_models import CreateTaskData
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_AUTH_FALLBACK,
    API_RESOURCE_NOT_FOUND,
    API_TASK_CANCELLED,
    API_TASK_CREATED_BY_MISMATCH,
    API_TASK_DELETED,
    API_TASK_MUTATION_FAILED,
    API_TASK_UPDATED,
)
from synthorg.observability.events.task import (
    TASK_CREATED,
    TASK_STATUS_CHANGED,
)

logger = get_logger(__name__)


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


def _map_task_engine_errors(
    exc: Exception,
    *,
    task_id: str | None = None,
) -> Exception:
    """Map a task-engine exception to the appropriate API error.

    Returns the API exception to raise (caller must ``raise`` it).

    Mapping:
        TaskNotFoundError           -> 404 NotFoundError
        TaskEngineNotRunningError   -> 503 ServiceUnavailableError
        TaskEngineQueueFullError    -> 503 ServiceUnavailableError
        TaskInternalError           -> 503 ServiceUnavailableError
        TaskVersionConflictError    -> 409 ConflictError
        TaskMutationError           -> 422 ApiValidationError
        Other                       -> 503 ServiceUnavailableError

    Args:
        exc: The engine exception to map.
        task_id: Optional task identifier for log context.

    Returns:
        The API exception to raise.
    """
    if isinstance(exc, TaskNotFoundError):
        if task_id is not None:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="task",
                id=task_id,
            )
        return NotFoundError(str(exc))
    if isinstance(exc, (TaskEngineNotRunningError, TaskEngineQueueFullError)):
        logger.error(
            API_TASK_MUTATION_FAILED,
            resource="task",
            task_id=task_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return ServiceUnavailableError("Service temporarily unavailable")
    if isinstance(exc, TaskInternalError):
        logger.error(
            API_TASK_MUTATION_FAILED,
            resource="task",
            task_id=task_id,
            error=str(exc),
            error_type="TaskInternalError",
        )
        return ServiceUnavailableError("Internal server error")
    if isinstance(exc, TaskVersionConflictError):
        logger.warning(
            API_TASK_MUTATION_FAILED,
            resource="task",
            task_id=task_id,
            error=str(exc),
            error_type="TaskVersionConflictError",
        )
        return ConflictError(str(exc))
    if isinstance(exc, TaskMutationError):
        logger.warning(
            API_TASK_MUTATION_FAILED,
            resource="task",
            task_id=task_id,
            error=str(exc),
            error_type="TaskMutationError",
        )
        return ApiValidationError(str(exc))
    # Unknown error type — log and wrap to prevent leaking internals
    logger.error(
        API_TASK_MUTATION_FAILED,
        resource="task",
        task_id=task_id,
        error=str(exc),
        error_type=type(exc).__name__,
    )
    return ServiceUnavailableError("Unexpected engine error")


class TaskController(Controller):
    """Full CRUD for tasks via ``TaskEngine``."""

    path = "/tasks"
    tags = ("tasks",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_tasks(  # noqa: PLR0913
        self,
        state: State,
        status: TaskStatus | None = None,
        assigned_to: str | None = None,
        project: str | None = None,
        offset: PaginationOffset = 0,
        limit: PaginationLimit = 50,
    ) -> PaginatedResponse[Task]:
        """List tasks with optional filters.

        Args:
            state: Application state.
            status: Filter by status.
            assigned_to: Filter by assignee.
            project: Filter by project.
            offset: Pagination offset.
            limit: Page size.

        Returns:
            Paginated task list.
        """
        app_state: AppState = state.app_state
        try:
            tasks, total = await app_state.task_engine.list_tasks(
                status=status,
                assigned_to=assigned_to,
                project=project,
            )
        except TaskInternalError as exc:
            raise _map_task_engine_errors(exc) from exc
        page, meta = paginate(
            tasks,
            offset=offset,
            limit=limit,
            total=total,
        )
        return PaginatedResponse(data=page, pagination=meta)

    @get("/{task_id:str}")
    async def get_task(
        self,
        state: State,
        task_id: str,
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
        try:
            task = await app_state.task_engine.get_task(task_id)
        except TaskInternalError as exc:
            raise _map_task_engine_errors(exc, task_id=task_id) from exc
        if task is None:
            msg = f"Task {task_id!r} not found"
            logger.warning(API_RESOURCE_NOT_FOUND, resource="task", id=task_id)
            raise NotFoundError(msg)
        return ApiResponse(data=task)

    @post(guards=[require_write_access], status_code=201)
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
        try:
            task = await app_state.task_engine.create_task(
                task_data,
                requested_by=requester,
            )
        except (
            TaskEngineNotRunningError,
            TaskEngineQueueFullError,
            TaskInternalError,
            TaskMutationError,
        ) as exc:
            raise _map_task_engine_errors(exc) from exc
        logger.info(
            TASK_CREATED,
            task_id=task.id,
            title=task.title,
        )
        return ApiResponse(data=task)

    @patch("/{task_id:str}", guards=[require_write_access])
    async def update_task(
        self,
        state: State,
        task_id: str,
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
        try:
            task = await app_state.task_engine.update_task(
                task_id,
                updates,
                requested_by=_extract_requester(state),
                expected_version=data.expected_version,
            )
        except (
            TaskEngineNotRunningError,
            TaskEngineQueueFullError,
            TaskNotFoundError,
            TaskVersionConflictError,
            TaskInternalError,
            TaskMutationError,
        ) as exc:
            raise _map_task_engine_errors(exc, task_id=task_id) from exc
        logger.info(API_TASK_UPDATED, task_id=task_id, fields=list(updates))
        return ApiResponse(data=task)

    @post(
        "/{task_id:str}/transition",
        guards=[require_write_access],
    )
    async def transition_task(
        self,
        state: State,
        task_id: str,
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
        try:
            task, from_status = await app_state.task_engine.transition_task(
                task_id,
                data.target_status,
                requested_by=requester,
                reason=f"API transition to {data.target_status.value}",
                expected_version=data.expected_version,
                **overrides,
            )
        except (
            TaskEngineNotRunningError,
            TaskEngineQueueFullError,
            TaskNotFoundError,
            TaskInternalError,
            TaskVersionConflictError,
            TaskMutationError,
        ) as exc:
            raise _map_task_engine_errors(exc, task_id=task_id) from exc
        logger.info(
            TASK_STATUS_CHANGED,
            task_id=task_id,
            from_status=from_status.value if from_status else None,
            to_status=task.status.value,
        )
        return ApiResponse(data=task)

    @delete("/{task_id:str}", guards=[require_write_access], status_code=200)
    async def delete_task(
        self,
        state: State,
        task_id: str,
    ) -> ApiResponse[None]:
        """Delete a task.

        Args:
            state: Application state.
            task_id: Task identifier.

        Returns:
            Success envelope.

        Raises:
            NotFoundError: If the task is not found.
        """
        app_state: AppState = state.app_state
        try:
            await app_state.task_engine.delete_task(
                task_id,
                requested_by=_extract_requester(state),
            )
        except (
            TaskEngineNotRunningError,
            TaskEngineQueueFullError,
            TaskNotFoundError,
            TaskInternalError,
            TaskMutationError,
        ) as exc:
            raise _map_task_engine_errors(exc, task_id=task_id) from exc
        logger.info(API_TASK_DELETED, task_id=task_id)
        return ApiResponse(data=None)

    @post("/{task_id:str}/cancel", guards=[require_write_access])
    async def cancel_task(
        self,
        state: State,
        task_id: str,
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
        try:
            task = await app_state.task_engine.cancel_task(
                task_id,
                requested_by=_extract_requester(state),
                reason=data.reason,
            )
        except (
            TaskEngineNotRunningError,
            TaskEngineQueueFullError,
            TaskNotFoundError,
            TaskInternalError,
            TaskMutationError,
        ) as exc:
            raise _map_task_engine_errors(exc, task_id=task_id) from exc
        logger.info(API_TASK_CANCELLED, task_id=task_id)
        return ApiResponse(data=task)
