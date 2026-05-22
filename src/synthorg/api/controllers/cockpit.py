"""Mission-control cockpit controller: live activity, flight recorder, intervention.

Live activity and flight-recorder reads are read-access; interventions
require write access. All endpoints 503 (via the ``AppState`` service
properties) until the cockpit services are wired after persistence
connects. Interventions are audit-logged via ``cockpit.intervention.*``.
"""

from typing import Final

from litestar import Controller, get, post
from litestar.datastructures import State  # noqa: TC002
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.path_params import PathId  # noqa: TC001
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.enums import InterventionKind, TaskStatus
from synthorg.core.task import Task  # noqa: TC001 -- response field type
from synthorg.core.types import NotBlankStr
from synthorg.engine.cockpit import LiveActivitySnapshot  # noqa: TC001
from synthorg.engine.flight_recording import ReplaySeekView  # noqa: TC001
from synthorg.engine.intervention import SteeringOutcome  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.cockpit import (
    COCKPIT_INTERVENTION_APPLIED,
    COCKPIT_INTERVENTION_INITIATED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrame,  # noqa: TC001 -- response field type
)

logger = get_logger(__name__)

_OPERATOR: Final[str] = "mission-control"
_COCKPIT_NS: Final[str] = "cockpit"


class FlightRecorderFramesResponse(BaseModel):
    """A page of flight-recorder frames for an execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: NotBlankStr = Field(description="Execution the frames belong to")
    frames: tuple[FlightRecorderFrame, ...] = Field(
        default=(),
        description="Frames newest-first",
    )


class PauseInterventionRequest(BaseModel):
    """Pause a running task (transition to INTERRUPTED)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: NotBlankStr = Field(description="Task to pause")
    reason: NotBlankStr = Field(description="Operator reason for the pause")


class KillInterventionRequest(BaseModel):
    """Kill a running task (cancel it)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: NotBlankStr = Field(description="Task to kill")
    reason: NotBlankStr = Field(description="Operator reason for the kill")


class SteerInterventionRequest(BaseModel):
    """Send a hint or redirect to a running agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: NotBlankStr = Field(description="Execution to steer")
    agent_id: NotBlankStr = Field(description="Agent to steer")
    text: NotBlankStr = Field(description="Operator hint / redirect text")


class CockpitController(Controller):
    """Live activity, flight-recorder replay, and operator interventions."""

    path = "/cockpit"
    tags = ("cockpit",)
    guards = [require_read_access]  # noqa: RUF012

    @get("/snapshot")
    async def get_snapshot(self, state: State) -> ApiResponse[LiveActivitySnapshot]:
        """Return the live org-activity snapshot."""
        app_state: AppState = state.app_state
        resolver = app_state.config_resolver
        stuck_idle_minutes = await resolver.get_float(
            _COCKPIT_NS, "stuck_idle_threshold_minutes"
        )
        runaway_cost_percent = await resolver.get_float(
            _COCKPIT_NS, "runaway_cost_threshold_percent"
        )
        snapshot = await app_state.cockpit_service.get_live_snapshot(
            stuck_idle_minutes=stuck_idle_minutes,
            runaway_cost_percent=runaway_cost_percent,
        )
        return ApiResponse(data=snapshot)

    @get("/flight-recorder/{execution_id:str}/frames")
    async def get_frames(
        self,
        state: State,
        execution_id: PathId,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> ApiResponse[FlightRecorderFramesResponse]:
        """Return the flight-recorder scrubber timeline (newest-first)."""
        app_state: AppState = state.app_state
        frames = await app_state.flight_recorder_service.get_frames(
            execution_id,
            limit=limit,
            offset=offset,
        )
        return ApiResponse(
            data=FlightRecorderFramesResponse(
                execution_id=NotBlankStr(execution_id),
                frames=frames,
            ),
        )

    @get("/flight-recorder/{execution_id:str}/seek/{turn_index:int}")
    async def seek_frame(
        self,
        state: State,
        execution_id: PathId,
        turn_index: int,
    ) -> ApiResponse[ReplaySeekView]:
        """Reconstruct scrubber state at a target turn."""
        app_state: AppState = state.app_state
        view = await app_state.flight_recorder_service.seek(execution_id, turn_index)
        return ApiResponse(data=view)

    @post("/interventions/pause", guards=[require_write_access])
    async def pause(
        self,
        state: State,
        data: PauseInterventionRequest,
    ) -> ApiResponse[Task]:
        """Pause a running task (transition to INTERRUPTED)."""
        app_state: AppState = state.app_state
        logger.info(
            COCKPIT_INTERVENTION_INITIATED,
            intervention_kind=InterventionKind.PAUSE.value,
            task_id=data.task_id,
        )
        task, _from = await app_state.task_engine.transition_task(
            data.task_id,
            TaskStatus.INTERRUPTED,
            requested_by=_OPERATOR,
            reason=data.reason,
        )
        logger.info(
            COCKPIT_INTERVENTION_APPLIED,
            intervention_kind=InterventionKind.PAUSE.value,
            task_id=data.task_id,
        )
        return ApiResponse(data=task)

    @post("/interventions/kill", guards=[require_write_access])
    async def kill(
        self,
        state: State,
        data: KillInterventionRequest,
    ) -> ApiResponse[Task]:
        """Kill a running task (cancel it)."""
        app_state: AppState = state.app_state
        logger.info(
            COCKPIT_INTERVENTION_INITIATED,
            intervention_kind=InterventionKind.KILL.value,
            task_id=data.task_id,
        )
        task, _prior = await app_state.task_engine.cancel_task(
            data.task_id,
            requested_by=_OPERATOR,
            reason=data.reason,
        )
        logger.info(
            COCKPIT_INTERVENTION_APPLIED,
            intervention_kind=InterventionKind.KILL.value,
            task_id=data.task_id,
        )
        return ApiResponse(data=task)

    @post("/interventions/hint", guards=[require_write_access])
    async def hint(
        self,
        state: State,
        data: SteerInterventionRequest,
    ) -> ApiResponse[SteeringOutcome]:
        """Queue a hint for a running agent."""
        return await self._steer(state, InterventionKind.HINT, data)

    @post("/interventions/redirect", guards=[require_write_access])
    async def redirect(
        self,
        state: State,
        data: SteerInterventionRequest,
    ) -> ApiResponse[SteeringOutcome]:
        """Queue a redirect for a running agent."""
        return await self._steer(state, InterventionKind.REDIRECT, data)

    async def _steer(
        self,
        state: State,
        kind: InterventionKind,
        data: SteerInterventionRequest,
    ) -> ApiResponse[SteeringOutcome]:
        """Route a hint/redirect through the steering directive."""
        app_state: AppState = state.app_state
        logger.info(
            COCKPIT_INTERVENTION_INITIATED,
            intervention_kind=kind.value,
            execution_id=data.execution_id,
            agent_id=data.agent_id,
        )
        outcome = await app_state.steering_directive.steer(
            kind=kind,
            execution_id=data.execution_id,
            agent_id=data.agent_id,
            details={"text": data.text},
        )
        logger.info(
            COCKPIT_INTERVENTION_APPLIED,
            intervention_kind=kind.value,
            execution_id=data.execution_id,
            agent_id=data.agent_id,
            applied=outcome.applied,
        )
        return ApiResponse(data=outcome)
