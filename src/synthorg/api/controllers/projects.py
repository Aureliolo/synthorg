"""Project controller -- endpoints for project listing, creation and deletion."""

import uuid
from typing import Annotated, Final

from litestar import Controller, Request, Response, delete, get, patch, post
from litestar.datastructures import State
from litestar.params import QueryParameter
from litestar.status_codes import HTTP_204_NO_CONTENT

from synthorg.api.channels import CHANNEL_PROJECTS, publish_ws_event
from synthorg.api.controllers._project_autonomy import (
    AutonomyModeTransition,
    ProjectAutonomyModeRequest,
    audit_autonomy_mode_change,
    guard_full_autonomy_optin,
)
from synthorg.api.controllers._project_cascade import cascade_supersede_children
from synthorg.api.controllers._requester import extract_requester
from synthorg.api.dto import (
    ApiResponse,
    CreateProjectRequest,
    PaginatedResponse,
)
from synthorg.api.guards import require_read_access, require_write_access, role_of
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.responses import require_resource_or_404
from synthorg.api.services.project_service import ProjectService
from synthorg.api.ws_models import WsEventType
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.domain_errors import (
    NotFoundError,
    ValidationError,
    VersionConflictError,
)
from synthorg.core.persistence_errors import PersistenceVersionConflictError
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_RESOURCE_NOT_FOUND,
    API_VALIDATION_FAILED,
)
from synthorg.persistence.state import persistence_of
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


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

    @patch(
        "/{project_id:str}/autonomy-mode",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("projects.update", key="user"),
        ],
    )
    async def set_autonomy_mode(
        self,
        request: Request[object, object, State],
        state: State,
        project_id: PathId,
        data: ProjectAutonomyModeRequest,
    ) -> Response[ApiResponse[Project]]:
        """Set (or clear) an initiative's operator-set autonomy mode.

        The mode becomes the initiative-level autonomy the SecOps gate
        resolves against, below a per-agent override and above the
        department/company default. A ``null`` mode clears the override.

        Transitioning INTO ``full`` (gate-off pass-through) disables the
        gate for the initiative's agents: it is a deliberate, CEO-only
        action requiring ``confirm=true`` and is audited at WARNING. The
        write is version-guarded, so a concurrent edit surfaces a 409
        rather than silently clobbering.

        Args:
            request: The incoming request (carries the acting role + user).
            state: Application state.
            project_id: Project identifier.
            data: Autonomy-mode payload.

        Returns:
            The updated project, or 404 if not found.

        Raises:
            ForbiddenError: A non-CEO attempted the transition to full.
            ValidationError: The transition to full lacked confirmation.
            VersionConflictError: A concurrent write moved the version.
        """
        service = _service(state)
        project = require_resource_or_404(
            await service.get(project_id),
            resource_type="Project",
            identifier=project_id,
            log_event=API_RESOURCE_NOT_FOUND,
            operation="update",
        )
        previous_mode = project.autonomy_mode
        # Clearing an override (mode=None) inherits the company default, which
        # can itself be full (gate-off). Guard and audit the EFFECTIVE resolved
        # mode so a clear-into-inherited-full can neither bypass the CEO opt-in
        # nor be mislabelled as gate-on. Department overrides are not yet a
        # resolution input, so the effective fallback is the company default.
        company_default = await config_resolver_of(state.app_state).get_enum(
            "company", "autonomy_level", AutonomyLevel
        )
        transition = AutonomyModeTransition(
            previous=previous_mode,
            new=data.mode,
            effective_previous=(
                previous_mode if previous_mode is not None else company_default
            ),
            effective_new=data.mode if data.mode is not None else company_default,
        )
        guard_full_autonomy_optin(
            role=role_of(request),
            transition=transition,
            confirm=data.confirm,
        )
        updated = project.model_copy(
            update={"autonomy_mode": data.mode, "version": project.version + 1},
        )
        expected = (
            data.expected_version
            if data.expected_version is not None
            else project.version
        )
        try:
            await service.update(updated, expected_version=expected)
        except PersistenceVersionConflictError as exc:
            msg = f"Project {project_id!r} was modified concurrently"
            raise VersionConflictError(msg) from exc
        audit_autonomy_mode_change(
            project_id=project_id,
            transition=transition,
            requested_by=extract_requester(state),
        )
        publish_ws_event(
            request,
            WsEventType.PROJECT_AUTONOMY_MODE_CHANGED,
            CHANNEL_PROJECTS,
            {
                "project_id": project_id,
                "new_mode": data.mode.value if data.mode is not None else None,
                "previous_mode": (
                    previous_mode.value if previous_mode is not None else None
                ),
                "new_version": updated.version,
            },
        )
        return Response(
            content=ApiResponse[Project](data=updated),
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
        await cascade_supersede_children(
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
