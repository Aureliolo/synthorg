"""Agile sprint endpoints at /sprints.

Read and drive sprints for ``agile_kanban`` orgs: list / fetch, create a
sprint, pull tasks into its backlog, and start / advance its lifecycle.
Delivery advances sprints automatically via the ceremony scheduler; these
endpoints are the explicit control + inspection surface.
"""

from typing import Annotated

from litestar import Controller, get, post
from litestar.datastructures import State
from litestar.params import QueryParameter
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import SprintNotFoundError
from synthorg.engine.state import sprint_service_of
from synthorg.engine.workflow.sprint_lifecycle import (
    STORY_POINTS_CEILING,
    Sprint,
    SprintStatus,
)

#: Upper bound on a caller-supplied id string at the API boundary.
_MAX_ID_LENGTH: int = 256


class SprintCreatePayload(BaseModel):
    """Request to create a new sprint."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project: NotBlankStr | None = Field(
        default=None,
        max_length=_MAX_ID_LENGTH,
        description="Owning project id; omit for an org-wide sprint",
    )


class SprintAddTaskPayload(BaseModel):
    """Request to pull a task into a sprint backlog."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task_id: NotBlankStr = Field(
        max_length=_MAX_ID_LENGTH,
        description="Task id to add to the backlog",
    )
    story_points: float = Field(
        default=0.0,
        ge=0.0,
        le=STORY_POINTS_CEILING,
        description="Story points committed for the task",
    )


class SprintController(Controller):
    """Agile sprint inspection + control endpoints."""

    path = "/sprints"
    tags = ("sprints",)
    guards = [require_read_access]  # noqa: RUF012

    @get("/")
    async def list_sprints(
        self,
        state: State,
        project: Annotated[
            str | None,
            QueryParameter(
                max_length=256,
                description="Filter to sprints for this project.",
            ),
        ] = None,
        status: Annotated[
            SprintStatus | None,
            QueryParameter(description="Filter to sprints in this status."),
        ] = None,
    ) -> ApiResponse[list[Sprint]]:
        """List sprints, filtered by optional project / status.

        Args:
            state: Litestar app state carrying the wired sprint service.
            project: Optional project filter.
            status: Optional lifecycle-status filter.

        Returns:
            ``ApiResponse[list[Sprint]]`` newest-first.
        """
        app_state: AppState = state.app_state
        sprints = await sprint_service_of(app_state).list_sprints(
            project=project, status=status
        )
        return ApiResponse(data=list(sprints))

    @get("/active")
    async def active_sprint(
        self,
        state: State,
        project: Annotated[
            str | None,
            QueryParameter(
                max_length=256,
                description="Project whose open sprint to fetch.",
            ),
        ] = None,
    ) -> ApiResponse[Sprint | None]:
        """Return the open (ACTIVE / IN_REVIEW) sprint for a project.

        Args:
            state: Litestar app state carrying the wired sprint service.
            project: Project whose open sprint to fetch (``None`` = org-wide).

        Returns:
            ``ApiResponse[Sprint | None]``; ``None`` when no sprint is open.
        """
        app_state: AppState = state.app_state
        sprint = await sprint_service_of(app_state).active_sprint(project)
        return ApiResponse(data=sprint)

    @get("/{sprint_id:str}")
    async def get_sprint(
        self,
        state: State,
        sprint_id: PathId,
    ) -> ApiResponse[Sprint]:
        """Fetch a sprint by id.

        Args:
            state: Litestar app state carrying the wired sprint service.
            sprint_id: The sprint id.

        Returns:
            ``ApiResponse[Sprint]``.

        Raises:
            SprintNotFoundError: When no sprint has that id (404).
        """
        app_state: AppState = state.app_state
        sprint = await sprint_service_of(app_state).get_sprint(sprint_id)
        if sprint is None:
            msg = f"Sprint {sprint_id!r} not found"
            raise SprintNotFoundError(msg)
        return ApiResponse(data=sprint)

    @post(
        "/",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("sprints.create", key="user"),
        ],
    )
    async def create_sprint(
        self,
        state: State,
        data: SprintCreatePayload,
    ) -> ApiResponse[Sprint]:
        """Create a new PLANNING sprint.

        Args:
            state: Litestar app state carrying the wired sprint service.
            data: The owning project (optional).

        Returns:
            ``ApiResponse[Sprint]`` for the created PLANNING sprint.
        """
        app_state: AppState = state.app_state
        sprint = await sprint_service_of(app_state).create_sprint(data.project)
        return ApiResponse(data=sprint)

    @post(
        "/{sprint_id:str}/tasks",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("sprints.add_task", key="user"),
        ],
    )
    async def add_task(
        self,
        state: State,
        sprint_id: PathId,
        data: SprintAddTaskPayload,
    ) -> ApiResponse[Sprint]:
        """Pull a task into a PLANNING sprint backlog.

        Args:
            state: Litestar app state carrying the wired sprint service.
            sprint_id: The sprint id.
            data: The task id and story points.

        Returns:
            ``ApiResponse[Sprint]`` after the task is added.

        Raises:
            SprintNotFoundError: When no sprint has that id (404).
            SprintTransitionConflictError: When the sprint is not
                ``PLANNING`` (409).
            SprintBacklogFullError: When the backlog is full (409).
        """
        app_state: AppState = state.app_state
        sprint = await sprint_service_of(app_state).add_task(
            sprint_id, data.task_id, data.story_points
        )
        return ApiResponse(data=sprint)

    @post(
        "/{sprint_id:str}/start",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("sprints.start", key="user"),
        ],
    )
    async def start_sprint(
        self,
        state: State,
        sprint_id: PathId,
    ) -> ApiResponse[Sprint]:
        """Start a PLANNING sprint (transition to ACTIVE + start ceremonies).

        Args:
            state: Litestar app state carrying the wired sprint service.
            sprint_id: The sprint id.

        Returns:
            ``ApiResponse[Sprint]`` for the started ACTIVE sprint.

        Raises:
            SprintNotFoundError: When no sprint has that id (404).
            SprintTransitionConflictError: When the sprint is not
                ``PLANNING`` (409).
        """
        app_state: AppState = state.app_state
        sprint = await sprint_service_of(app_state).start_sprint(sprint_id)
        return ApiResponse(data=sprint)

    @post(
        "/{sprint_id:str}/advance",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("sprints.advance", key="user"),
        ],
    )
    async def advance_sprint(
        self,
        state: State,
        sprint_id: PathId,
    ) -> ApiResponse[Sprint]:
        """Advance a sprint one hop along its linear lifecycle.

        Args:
            state: Litestar app state carrying the wired sprint service.
            sprint_id: The sprint id.

        Returns:
            ``ApiResponse[Sprint]`` for the advanced sprint.

        Raises:
            SprintNotFoundError: When no sprint has that id (404).
            SprintTransitionConflictError: When the sprint is terminal or
                the CAS finds a different state (409).
        """
        app_state: AppState = state.app_state
        sprint = await sprint_service_of(app_state).advance_sprint(sprint_id)
        return ApiResponse(data=sprint)
