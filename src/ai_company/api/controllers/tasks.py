"""Task controller — full CRUD via TaskRepository."""

from uuid import uuid4

from litestar import Controller, delete, get, patch, post
from litestar.datastructures import State  # noqa: TC002

from ai_company.api.dto import (
    ApiResponse,
    CreateTaskRequest,
    PaginatedResponse,
    TransitionTaskRequest,
    UpdateTaskRequest,
)
from ai_company.api.errors import NotFoundError
from ai_company.api.guards import require_write_access
from ai_company.api.pagination import PaginationLimit, PaginationOffset, paginate
from ai_company.api.state import AppState  # noqa: TC001
from ai_company.core.enums import TaskStatus  # noqa: TC001
from ai_company.core.task import Task
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
    """Full CRUD for tasks via ``TaskRepository``."""

    path = "/tasks"
    tags = ("tasks",)

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
        tasks = await app_state.persistence.tasks.list_tasks(
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
        task = await app_state.persistence.tasks.get(task_id)
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
        task_id = f"task-{uuid4().hex[:12]}"
        task = Task(
            id=task_id,
            title=data.title,
            description=data.description,
            type=data.type,
            priority=data.priority,
            project=data.project,
            created_by=data.created_by,
            estimated_complexity=data.estimated_complexity,
            budget_limit=data.budget_limit,
        )
        await app_state.persistence.tasks.save(task)
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
        task = await app_state.persistence.tasks.get(task_id)
        if task is None:
            msg = f"Task {task_id!r} not found"
            logger.warning(API_RESOURCE_NOT_FOUND, resource="task", id=task_id)
            raise NotFoundError(msg)

        updates = data.model_dump(exclude_none=True)
        if updates:
            task = task.model_copy(update=updates)
            await app_state.persistence.tasks.save(task)
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
        task = await app_state.persistence.tasks.get(task_id)
        if task is None:
            msg = f"Task {task_id!r} not found"
            logger.warning(API_RESOURCE_NOT_FOUND, resource="task", id=task_id)
            raise NotFoundError(msg)

        overrides: dict[str, object] = {}
        if data.assigned_to is not None:
            overrides["assigned_to"] = data.assigned_to

        new_task = task.with_transition(data.target_status, **overrides)
        await app_state.persistence.tasks.save(new_task)
        logger.info(
            TASK_STATUS_CHANGED,
            task_id=task_id,
            from_status=task.status.value,
            to_status=new_task.status.value,
        )
        return ApiResponse(data=new_task)

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
        deleted = await app_state.persistence.tasks.delete(task_id)
        if not deleted:
            msg = f"Task {task_id!r} not found"
            logger.warning(API_RESOURCE_NOT_FOUND, resource="task", id=task_id)
            raise NotFoundError(msg)
        logger.info(API_TASK_DELETED, task_id=task_id)
        return ApiResponse(data=None)
