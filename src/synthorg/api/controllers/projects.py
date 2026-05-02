"""Project controller -- endpoints for project listing, creation and deletion."""

import uuid
from typing import Annotated, Any

from litestar import Controller, Request, Response, delete, get, post
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter
from litestar.status_codes import HTTP_204_NO_CONTENT

from synthorg.api.channels import CHANNEL_PROJECTS, publish_ws_event
from synthorg.api.dto import (
    ApiResponse,
    CreateProjectRequest,
    PaginatedResponse,
)
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import CursorLimit, CursorParam, paginate_cursor
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.api.responses import require_resource_or_404
from synthorg.api.services.project_service import ProjectService
from synthorg.api.ws_models import WsEventType
from synthorg.core.domain_errors import NotFoundError, ValidationError
from synthorg.core.enums import ProjectStatus
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_RESOURCE_NOT_FOUND,
    API_VALIDATION_FAILED,
)

logger = get_logger(__name__)


def _service(state: State) -> ProjectService:
    """Build the per-request :class:`ProjectService` instance."""
    return ProjectService(repo=state.app_state.persistence.projects)


ProjectStatusFilter = Annotated[
    NotBlankStr | None,
    Parameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description="Filter by project status",
    ),
]

LeadFilter = Annotated[
    NotBlankStr | None,
    Parameter(
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
        limit: CursorLimit = 50,
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

        projects = await _service(state).list_projects(
            status=parsed_status,
            lead=lead,
        )
        page, meta = paginate_cursor(
            projects,
            limit=limit,
            cursor=cursor,
            secret=state.app_state.cursor_secret,
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
        guards=[require_write_access],
        status_code=HTTP_204_NO_CONTENT,
    )
    async def delete_project(
        self,
        request: Request[Any, Any, Any],
        state: State,
        project_id: PathId,
    ) -> None:
        """Delete a project by ID.

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

    @post(guards=[require_write_access])
    async def create_project(
        self,
        request: Request[Any, Any, Any],
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
            id=f"proj-{uuid.uuid4().hex[:12]}",
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
                "project_id": created.id,
                "name": created.name,
                "status": created.status.value,
                "lead": created.lead,
            },
        )
        return Response(
            content=ApiResponse[Project](data=created),
            status_code=201,
        )
