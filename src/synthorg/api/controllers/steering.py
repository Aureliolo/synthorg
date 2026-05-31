"""Mission-control steering controller: project-scoped mid-flight directives.

The operator issues a steering directive (a hint or a redirect) against a
project; every in-flight and newly-spawned agent on that project adopts it at
its next safe boundary. Issuing records the directive in the project brain and,
in ``EXPLICIT`` mode, cancels operator-specified obsolete tasks; ``PROPOSE``
mode returns a refined obsolete set for the operator to confirm via the
supersede endpoint. All writes require write access and 503 (via the
``CockpitStateSlice`` service property) until the steering service is wired
after the project brain connects.

The operator directive text is stored raw in the brain: the prompt-safety
envelope is applied at each LLM sink (``loop_hook`` wraps with
``TAG_BRAIN_STATE`` on re-injection; the proposer wraps candidate task data with
``TAG_TASK_DATA``), so wrapping here would double-wrap the persisted record and
corrupt the operator board display.
"""

from typing import Annotated, Final, Self

from litestar import Controller, get, post
from litestar.datastructures import State
from litestar.params import Parameter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg._core.features import require_service
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.path_params import PathId
from synthorg.api.state import AppState
from synthorg.core.enums import InterventionKind
from synthorg.core.types import NotBlankStr
from synthorg.engine.cockpit.state import CockpitStateSlice
from synthorg.engine.intervention import (
    ActiveSteeringDirective,
    SteeringIssueResult,
    SupersedeMode,
)
from synthorg.engine.intervention.models import STEERABLE_KINDS
from synthorg.observability import get_logger
from synthorg.observability.events.cockpit import (
    COCKPIT_INTERVENTION_APPLIED,
    COCKPIT_INTERVENTION_INITIATED,
)
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)

_OPERATOR: Final[str] = "mission-control"
_COCKPIT_NS: Final[str] = "cockpit"
_MAX_ACTIVE_KEY: Final[str] = "steering_max_active_directives"


class IssueSteeringRequest(BaseModel):
    """Issue a project-scoped steering directive (hint or redirect)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Project the directive targets")
    kind: InterventionKind = Field(description="HINT (advisory) or REDIRECT (replan)")
    text: NotBlankStr = Field(description="The operator directive text")
    narrow_task_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Optional task-id narrowing; empty means project-wide",
    )
    narrow_agent_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Optional agent-id narrowing; empty means every agent",
    )
    supersede_task_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Tasks to treat as obsolete (EXPLICIT cancels, PROPOSE seeds)",
    )
    supersede_mode: SupersedeMode = Field(
        default=SupersedeMode.NONE,
        description="How obsolete tasks are handled (none / explicit / propose)",
    )

    @model_validator(mode="after")
    def _validate_steerable_kind(self) -> Self:
        """Reject PAUSE/KILL: only HINT and REDIRECT are steerable.

        Returns:
            ``self`` when the kind is steerable.

        Raises:
            ValueError: When ``kind`` is not a steerable intervention.
        """
        if self.kind not in STEERABLE_KINDS:
            msg = f"{self.kind.value!r} is not a steerable directive kind"
            raise ValueError(msg)
        return self


class ConfirmSupersessionRequest(BaseModel):
    """Confirm (and optionally edit) the obsolete-task set for a directive."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Project the directive targets")
    task_ids: tuple[NotBlankStr, ...] = Field(
        description="Operator-confirmed obsolete tasks to cancel",
    )


class SteeringSupersessionResult(BaseModel):
    """Outcome of confirming a supersession: the tasks actually cancelled."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    directive_id: NotBlankStr = Field(description="The directive confirmed")
    cancelled_task_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Tasks cancelled via the single-writer task engine",
    )


class SteeringController(Controller):
    """Project-scoped issue / list / supersede for mid-flight steering."""

    path = "/cockpit/steering"
    tags = ("cockpit",)
    guards = [require_read_access]  # noqa: RUF012

    @post(guards=[require_write_access])
    async def issue(
        self,
        state: State,
        data: IssueSteeringRequest,
    ) -> ApiResponse[SteeringIssueResult]:
        """Issue a steering directive against a project.

        Returns:
            ``ApiResponse[SteeringIssueResult]`` instance.
        """
        app_state: AppState = state.app_state
        logger.info(
            COCKPIT_INTERVENTION_INITIATED,
            intervention_kind=data.kind.value,
            project_id=data.project_id,
        )
        steering = require_service(
            app_state.slice(CockpitStateSlice).steering_service, "Steering Service"
        )
        result = await steering.issue(
            project_id=data.project_id,
            kind=data.kind,
            text=data.text,
            author=NotBlankStr(_OPERATOR),
            narrow_task_ids=data.narrow_task_ids,
            narrow_agent_ids=data.narrow_agent_ids,
            supersede_task_ids=data.supersede_task_ids,
            supersede_mode=data.supersede_mode,
        )
        logger.info(
            COCKPIT_INTERVENTION_APPLIED,
            intervention_kind=data.kind.value,
            project_id=data.project_id,
            directive_id=result.directive_id,
        )
        return ApiResponse(data=result)

    @get()
    async def list_active(
        self,
        state: State,
        project_id: Annotated[
            str, Parameter(description="Project the directives target")
        ],
    ) -> ApiResponse[list[ActiveSteeringDirective]]:
        """List the active steering directives for a project (operator board).

        Returns:
            ``ApiResponse[list[ActiveSteeringDirective]]`` instance.
        """
        app_state: AppState = state.app_state
        resolver = config_resolver_of(app_state)
        limit = await resolver.get_int(_COCKPIT_NS, _MAX_ACTIVE_KEY)
        steering = require_service(
            app_state.slice(CockpitStateSlice).steering_service, "Steering Service"
        )
        directives = await steering.list_active(
            project_id=NotBlankStr(project_id),
            limit=limit,
        )
        return ApiResponse(data=list(directives))

    @post("/{directive_id:str}/supersede", guards=[require_write_access])
    async def supersede(
        self,
        state: State,
        directive_id: PathId,
        data: ConfirmSupersessionRequest,
    ) -> ApiResponse[SteeringSupersessionResult]:
        """Confirm the operator-edited obsolete-task set for a directive.

        Returns:
            ``ApiResponse[SteeringSupersessionResult]`` instance.
        """
        app_state: AppState = state.app_state
        logger.info(
            COCKPIT_INTERVENTION_INITIATED,
            intervention_kind="steering_supersede",
            project_id=data.project_id,
            directive_id=directive_id,
        )
        steering = require_service(
            app_state.slice(CockpitStateSlice).steering_service, "Steering Service"
        )
        cancelled = await steering.confirm_supersession(
            project_id=data.project_id,
            directive_id=NotBlankStr(directive_id),
            task_ids=data.task_ids,
            author=NotBlankStr(_OPERATOR),
        )
        logger.info(
            COCKPIT_INTERVENTION_APPLIED,
            intervention_kind="steering_supersede",
            project_id=data.project_id,
            directive_id=directive_id,
            cancelled_count=len(cancelled),
        )
        return ApiResponse(
            data=SteeringSupersessionResult(
                directive_id=NotBlankStr(directive_id),
                cancelled_task_ids=cancelled,
            )
        )
