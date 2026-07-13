"""Project controller -- endpoints for project listing, creation and deletion."""

import uuid
from typing import Annotated, Final

from litestar import Controller, Request, Response, delete, get, post
from litestar.datastructures import State
from litestar.params import QueryParameter
from litestar.status_codes import HTTP_204_NO_CONTENT

from synthorg.api.channels import CHANNEL_PROJECTS, publish_ws_event
from synthorg.api.controllers._requester import extract_requester
from synthorg.api.dto import (
    ApiResponse,
    CreateProjectRequest,
    PaginatedResponse,
)
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.responses import require_resource_or_404
from synthorg.api.services.plan_service import PlanService
from synthorg.api.services.project_service import ProjectService
from synthorg.api.state import AppState
from synthorg.api.ws_models import WsEventType
from synthorg.core.domain_errors import NotFoundError, ValidationError
from synthorg.core.pagination import DEFAULT_PAGE_SIZE
from synthorg.core.plan_enums import REWORKABLE_STATUSES, PlanStatus
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.task_transitions import VALID_TRANSITIONS
from synthorg.core.types import NotBlankStr
from synthorg.engine.state import task_engine_of
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_apply_helpers import TRULY_TERMINAL_STATUSES
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_RESOURCE_NOT_FOUND,
    API_VALIDATION_FAILED,
)
from synthorg.persistence.plan_protocol import PlanFilterSpec
from synthorg.persistence.state import persistence_of
from synthorg.persistence.task_protocol import TaskFilterSpec

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50
_CASCADE_REASON: Final[str] = "project deleted"


async def _cascade_supersede_children(
    app_state: AppState,
    project_id: NotBlankStr,
    *,
    requested_by: str,
) -> None:
    """Supersede a project's live plans and cancel its open tasks before delete.

    A project delete must never orphan its children: every non-terminal plan is
    superseded (a review decision that will now never come) and every
    non-terminal task is cancelled, each through its audited lifecycle
    transition, so no row is left pointing at a deleted project.

    The cascade and the subsequent delete run as separate audited operations,
    not one database transaction: the task-engine transitions emit domain
    events that cannot be rolled back, and no unit-of-work seam spans the plan
    service, the task engine, and the project repository. Consistency comes from
    idempotent forward-recovery instead: the cascade only acts on non-terminal
    children (already-terminal ones are skipped) and the delete runs only after
    it fully succeeds, so a mid-cascade failure or a failed delete leaves a
    retriable, never-orphaning state: re-issuing the delete re-runs the cascade
    as a no-op over the already-resolved children and removes the project. The
    teardown assumes no concurrent child creation for the project being deleted
    (child creation requires a live project; a delete is an exclusive operator
    action), so paginating the existing children is sufficient.

    Args:
        app_state: Application state (carries persistence, clock, task engine).
        project_id: The project whose children are being resolved.
        requested_by: Identity recorded on each task cancellation.
    """
    persistence = persistence_of(app_state)
    plan_service = PlanService(repo=persistence.plans, clock=app_state.clock)
    offset = 0
    # lint-allow: long-running-loop-kill-switch -- bounded child pagination
    while True:
        plans = await persistence.plans.query(
            PlanFilterSpec(project=project_id),
            limit=DEFAULT_PAGE_SIZE,
            offset=offset,
        )
        for plan in plans:
            if plan.status in REWORKABLE_STATUSES:
                await plan_service.sync_status(
                    plan,
                    PlanStatus.SUPERSEDED,
                    requested_by=requested_by,
                    reason=_CASCADE_REASON,
                )
        if len(plans) < DEFAULT_PAGE_SIZE:
            break
        offset += DEFAULT_PAGE_SIZE

    task_engine = task_engine_of(app_state)
    offset = 0
    # lint-allow: long-running-loop-kill-switch -- bounded child pagination
    while True:
        tasks = await persistence.tasks.query(
            TaskFilterSpec(project=project_id),
            limit=DEFAULT_PAGE_SIZE,
            offset=offset,
        )
        for task in tasks:
            if task.status not in TRULY_TERMINAL_STATUSES:
                await _terminate_project_task(
                    task_engine, task, requested_by=requested_by
                )
        if len(tasks) < DEFAULT_PAGE_SIZE:
            break
        offset += DEFAULT_PAGE_SIZE


async def _terminate_project_task(
    task_engine: TaskEngine,
    task: Task,
    *,
    requested_by: str,
) -> None:
    """Move a non-terminal task to a terminal state on project delete.

    The task lifecycle forbids ``CREATED -> CANCELLED`` (a created task is
    rejected, not cancelled) and lets the stuck states (blocked / failed /
    interrupted / suspended) reach a terminal only via ``ASSIGNED``. This
    routes each task to the correct terminal so no live work dangles against
    the deleted project, and every task keeps its audit row.

    Args:
        task_engine: Engine driving the audited status transitions.
        task: The non-terminal task to terminate.
        requested_by: Identity recorded on each transition.
    """
    target = (
        TaskStatus.REJECTED
        if task.status is TaskStatus.CREATED
        else TaskStatus.CANCELLED
    )
    if target not in VALID_TRANSITIONS[task.status]:
        # A stuck state can only reach a terminal through ASSIGNED; hop there
        # first (the task keeps its assignee), then cancel.
        await task_engine.transition_task(
            str(task.id),
            TaskStatus.ASSIGNED,
            requested_by=requested_by,
            reason=_CASCADE_REASON,
        )
        target = TaskStatus.CANCELLED
    await task_engine.transition_task(
        str(task.id),
        target,
        requested_by=requested_by,
        reason=_CASCADE_REASON,
    )


def _service(state: State) -> ProjectService:
    """Build the per-request :class:`ProjectService` instance.

    Returns:
        ``ProjectService`` instance.
    """
    return ProjectService(repo=persistence_of(state.app_state).projects)


ProjectStatusFilter = Annotated[
    NotBlankStr | None,
    QueryParameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description="Filter by project status",
    ),
]

LeadFilter = Annotated[
    NotBlankStr | None,
    QueryParameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description="Filter by project lead agent ID",
    ),
]


class ProjectController(Controller):
    """Controller for project listing, creation, and deletion."""

    path = "/projects"
    tags = ("projects",)

    @get(guards=[require_read_access])
    async def list_projects(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
        status: ProjectStatusFilter = None,
        lead: LeadFilter = None,
    ) -> PaginatedResponse[Project]:
        """List projects with optional filters.

        Args:
            state: Application state.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.
            status: Filter by project status.
            lead: Filter by project lead agent ID.

        Returns:
            Paginated list of projects.

        Raises:
            ValidationError: ``status`` is not a valid
                :class:`ProjectStatus` value.
        """
        parsed_status: ProjectStatus | None = None
        if status is not None:
            try:
                parsed_status = ProjectStatus(status)
            except ValueError as exc:
                valid = ", ".join(e.value for e in ProjectStatus)
                msg = f"Invalid project status: {status!r}. Valid values: {valid}"
                logger.warning(
                    API_VALIDATION_FAILED,
                    reason="invalid_project_status",
                    status=status,
                    valid=valid,
                )
                raise ValidationError(msg) from exc

        # Over-fetch by one page so the cursor paginator can detect
        # has_more without a separate COUNT round-trip. ``limit + 1``
        # caps repository-side scans at the operator-tunable page size.
        projects = await _service(state).list_projects(
            status=parsed_status,
            lead=lead,
            limit=limit + 1,
        )
        page, meta = paginate_cursor(
            projects,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(state.app_state),
        )
        return PaginatedResponse[Project](data=page, pagination=meta)

    @get("/{project_id:str}", guards=[require_read_access])
    async def get_project(
        self,
        state: State,
        project_id: PathId,
    ) -> Response[ApiResponse[Project]]:
        """Get a project by ID.

        Args:
            state: Application state.
            project_id: Project identifier.

        Returns:
            The project, or 404 if not found.
        """
        project = require_resource_or_404(
            await _service(state).get(project_id),
            resource_type="Project",
            identifier=project_id,
            log_event=API_RESOURCE_NOT_FOUND,
            operation="read",
        )
        return Response(
            content=ApiResponse[Project](data=project),
            status_code=200,
        )

    @delete(
        "/{project_id:str}",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("projects.delete", key="user"),
        ],
        status_code=HTTP_204_NO_CONTENT,
    )
    async def delete_project(
        self,
        request: Request[object, object, State],
        state: State,
        project_id: PathId,
    ) -> None:
        """Delete a project by ID, cascading to its plans and tasks.

        A project delete supersedes the project's live (non-terminal) plans and
        cancels its open tasks first, so deletion never leaves a plan or task
        orphaned against a project that no longer exists.

        Args:
            request: The incoming request.
            state: Application state.
            project_id: Project identifier.

        Raises:
            NotFoundError: Project with ``project_id`` does not exist.
        """
        service = _service(state)
        project = require_resource_or_404(
            await service.get(project_id),
            resource_type="Project",
            identifier=project_id,
            log_event=API_RESOURCE_NOT_FOUND,
            operation="delete",
            extra_log_kwargs={"project_id": project_id},
        )
        await _cascade_supersede_children(
            state.app_state,
            project_id,
            requested_by=extract_requester(state),
        )
        deleted = await service.delete(project_id)
        if not deleted:
            # Race: row disappeared between get() and delete(). Log as a
            # warning so concurrent destructive operations stay in the audit
            # trail.
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="project",
                project_id=project_id,
                operation="delete",
                note="concurrent_delete",
            )
            msg = f"Project {project_id!r} not found"
            raise NotFoundError(msg)
        publish_ws_event(
            request,
            WsEventType.PROJECT_DELETED,
            CHANNEL_PROJECTS,
            {"project_id": project_id, "name": project.name},
        )

    @post(
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("projects.create", key="user"),
        ],
    )
    async def create_project(
        self,
        request: Request[object, object, State],
        state: State,
        data: CreateProjectRequest,
    ) -> Response[ApiResponse[Project]]:
        """Create a new project.

        Args:
            request: The incoming request.
            state: Application state.
            data: Project creation payload.

        Returns:
            The created project with generated ID.
        """
        project = Project(
            id=uuid.uuid4(),
            name=data.name,
            description=data.description,
            team=data.team,
            lead=data.lead,
            deadline=data.deadline,
            budget=data.budget,
        )
        created = await _service(state).create(project)
        publish_ws_event(
            request,
            WsEventType.PROJECT_CREATED,
            CHANNEL_PROJECTS,
            {
                "project_id": str(created.id),
                "name": created.name,
                "status": created.status.value,
                "lead": created.lead,
            },
        )
        return Response(
            content=ApiResponse[Project](data=created),
            status_code=201,
        )
