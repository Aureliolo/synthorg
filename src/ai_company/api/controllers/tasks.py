"""Task controller — full CRUD via TaskEngine."""

from litestar import Controller, delete, get, patch, post
from litestar.datastructures import State  # noqa: TC002

from ai_company.api.dto import (
    ApiResponse,
    CreateTaskRequest,
    PaginatedResponse,
    TransitionTaskRequest,
    UpdateTaskRequest,
)
from ai_company.api.errors import ApiValidationError, NotFoundError
from ai_company.api.guards import require_read_access, require_write_access
from ai_company.api.pagination import PaginationLimit, PaginationOffset, paginate
from ai_company.api.state import AppState  # noqa: TC001
from ai_company.core.enums import TaskStatus  # noqa: TC001
from ai_company.core.task import Task  # noqa: TC001
from ai_company.engine.errors import TaskMutationError
from ai_company.engine.task_engine_models import CreateTaskData
from ai_company.observability import get_logger
from ai_company.observability.events.api import (
    API_RESOURCE_NOT_FOUND,
    API_TASK_DELETED,
    API_TASK_UPDATED,
)
from ai_company.observability.events.task import (
    TASK_CREATED,
    TASK_STATUS_CHANGED,
)

logger = get_logger(__name__)


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
        tasks = await app_state.task_engine.list_tasks(
            status=status,
            assigned_to=assigned_to,
            project=project,
        )
        page, meta = paginate(tasks, offset=offset, limit=limit)
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
        task = await app_state.task_engine.get_task(task_id)
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
        task = await app_state.task_engine.create_task(
            task_data,
            requested_by=data.created_by,
        )
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
        updates = data.model_dump(exclude_none=True)
        try:
            task = await app_state.task_engine.update_task(
                task_id,
                updates,
                requested_by="api",
            )
        except TaskMutationError as exc:
            if "not found" in str(exc):
                logger.warning(
                    API_RESOURCE_NOT_FOUND,
                    resource="task",
                    id=task_id,
                )
                raise NotFoundError(str(exc)) from exc
            raise
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
        try:
            task = await app_state.task_engine.transition_task(
                task_id,
                data.target_status,
                requested_by="api",
                reason=f"API transition to {data.target_status.value}",
                assigned_to=data.assigned_to,
            )
        except TaskMutationError as exc:
            error_str = str(exc)
            if "not found" in error_str:
                logger.warning(
                    API_RESOURCE_NOT_FOUND,
                    resource="task",
                    id=task_id,
                )
                raise NotFoundError(error_str) from exc
            logger.warning(
                TASK_STATUS_CHANGED,
                task_id=task_id,
                error=error_str,
            )
            raise ApiValidationError(error_str) from exc
        logger.info(
            TASK_STATUS_CHANGED,
            task_id=task_id,
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
                requested_by="api",
            )
        except TaskMutationError as exc:
            if "not found" in str(exc):
                msg = f"Task {task_id!r} not found"
                logger.warning(
                    API_RESOURCE_NOT_FOUND,
                    resource="task",
                    id=task_id,
                )
                raise NotFoundError(msg) from exc
            raise
        logger.info(API_TASK_DELETED, task_id=task_id)
        return ApiResponse(data=None)
