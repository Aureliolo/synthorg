"""Mission-control cockpit controller: live activity, flight recorder, intervention.

Live activity and flight-recorder reads are read-access; interventions
require write access. All endpoints 503 (via the ``AppState`` service
properties) until the cockpit services are wired after persistence
connects. Interventions are audit-logged via ``cockpit.intervention.*``.
"""

from typing import Annotated, Final

from litestar import Controller, get, post
from litestar.datastructures import State  # noqa: TC002
from litestar.params import PathParameter
from pydantic import BaseModel, ConfigDict, Field

from synthorg._core.features import require_service
from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import DEFAULT_LIMIT, ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    encode_countless_seek_meta,
)
from synthorg.api.path_params import PathId  # noqa: TC001
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.enums import InterventionKind, TaskStatus
from synthorg.core.task import Task  # noqa: TC001 -- response field type
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.cockpit import LiveActivitySnapshot  # noqa: TC001
from synthorg.engine.cockpit.state import CockpitStateSlice
from synthorg.engine.flight_recording import ReplaySeekView  # noqa: TC001
from synthorg.engine.intervention import SteeringOutcome  # noqa: TC001
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.engine.state import EngineStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.cockpit import (
    COCKPIT_INTERVENTION_APPLIED,
    COCKPIT_INTERVENTION_INITIATED,
)
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrame,
)
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)

_OPERATOR: Final[str] = "mission-control"
_COCKPIT_NS: Final[str] = "cockpit"

#: Litestar-validated annotated form for the ``turn_index`` path param so
#: a negative value is rejected at request parsing instead of leaking
#: into the repository as an invalid filter bound. ``ge=1`` matches the
#: ``FlightRecorderFrame.turn_index`` invariant.
TurnIndexPath = Annotated[
    int,
    PathParameter(ge=1, description="Target turn index (1-based)"),
]


class PauseInterventionRequest(BaseModel):
    """Pause a running task (transition to INTERRUPTED)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task_id: NotBlankStr = Field(description="Task to pause")
    reason: NotBlankStr = Field(description="Operator reason for the pause")


class KillInterventionRequest(BaseModel):
    """Kill a running task (cancel it)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task_id: NotBlankStr = Field(description="Task to kill")
    reason: NotBlankStr = Field(description="Operator reason for the kill")


class SteerInterventionRequest(BaseModel):
    """Send a hint or redirect to a running agent."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
        """Return the live org-activity snapshot.

        Returns:
            ``ApiResponse[LiveActivitySnapshot]`` instance.
        """
        app_state: AppState = state.app_state
        resolver = config_resolver_of(app_state)
        stuck_idle_minutes = await resolver.get_float(
            _COCKPIT_NS, "stuck_idle_threshold_minutes"
        )
        runaway_cost_percent = await resolver.get_float(
            _COCKPIT_NS, "runaway_cost_threshold_percent"
        )
        cockpit = require_service(
            app_state.slice(CockpitStateSlice).cockpit_service, "Cockpit Service"
        )
        snapshot = await cockpit.get_live_snapshot(
            stuck_idle_minutes=stuck_idle_minutes,
            runaway_cost_percent=runaway_cost_percent,
        )
        return ApiResponse(data=snapshot)

    @get("/flight-recorder/{execution_id:str}/frames")
    async def get_frames(
        self,
        state: State,
        execution_id: PathId,
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
    ) -> PaginatedResponse[FlightRecorderFrame]:
        """Return the flight-recorder scrubber timeline (newest-first, paginated).

        Uses opaque cursor pagination (``cursor`` + ``limit``) per the
        web dashboard's MANDATORY pagination contract; offset-based
        paging is gone. The underlying repo still slices on offset
        internally, but the cursor is HMAC-signed so the client treats
        it as opaque.

        Returns:
            ``PaginatedResponse[FlightRecorderFrame]`` instance.
        """
        app_state: AppState = state.app_state
        offset = (
            0
            if cursor is None
            else decode_cursor(cursor, secret=cursor_secret_of(app_state))
        )
        # Fetch ``limit + 1`` so we can detect that another page follows
        # without paying a separate COUNT round-trip on the frames table.
        recorder = require_service(
            app_state.slice(CockpitStateSlice).flight_recorder_service,
            "Flight Recorder Service",
        )
        frames = await recorder.get_frames(
            execution_id,
            limit=limit + 1,
            offset=offset,
        )
        meta = encode_countless_seek_meta(
            offset=offset,
            fetched_rows=len(frames),
            limit=limit,
            secret=cursor_secret_of(app_state),
        )
        window = tuple(frames[:limit])
        return PaginatedResponse[FlightRecorderFrame](data=window, pagination=meta)

    @get("/flight-recorder/{execution_id:str}/seek/{turn_index:int}")
    async def seek_frame(
        self,
        state: State,
        execution_id: PathId,
        turn_index: TurnIndexPath,
    ) -> ApiResponse[ReplaySeekView]:
        """Reconstruct scrubber state at a target turn.

        Returns:
            ``ApiResponse[ReplaySeekView]`` instance.
        """
        app_state: AppState = state.app_state
        recorder = require_service(
            app_state.slice(CockpitStateSlice).flight_recorder_service,
            "Flight Recorder Service",
        )
        view = await recorder.seek(execution_id, turn_index)
        return ApiResponse(data=view)

    @post("/interventions/pause", guards=[require_write_access])
    async def pause(
        self,
        state: State,
        data: PauseInterventionRequest,
    ) -> ApiResponse[Task]:
        """Pause a running task (transition to INTERRUPTED).

        Returns:
            ``ApiResponse[Task]`` instance.
        """
        app_state: AppState = state.app_state
        logger.info(
            COCKPIT_INTERVENTION_INITIATED,
            intervention_kind=InterventionKind.PAUSE.value,
            task_id=data.task_id,
        )
        task, _from = await require_service(
            app_state.slice(EngineStateSlice).task_engine, "Task Engine"
        ).transition_task(
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
        """Kill a running task (cancel it).

        Returns:
            ``ApiResponse[Task]`` instance.
        """
        app_state: AppState = state.app_state
        logger.info(
            COCKPIT_INTERVENTION_INITIATED,
            intervention_kind=InterventionKind.KILL.value,
            task_id=data.task_id,
        )
        task, _prior = await require_service(
            app_state.slice(EngineStateSlice).task_engine, "Task Engine"
        ).cancel_task(
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
        """Queue a hint for a running agent.

        Returns:
            ``ApiResponse[SteeringOutcome]`` instance.
        """
        return await self._steer(state, InterventionKind.HINT, data)

    @post("/interventions/redirect", guards=[require_write_access])
    async def redirect(
        self,
        state: State,
        data: SteerInterventionRequest,
    ) -> ApiResponse[SteeringOutcome]:
        """Queue a redirect for a running agent.

        Returns:
            ``ApiResponse[SteeringOutcome]`` instance.
        """
        return await self._steer(state, InterventionKind.REDIRECT, data)

    async def _steer(
        self,
        state: State,
        kind: InterventionKind,
        data: SteerInterventionRequest,
    ) -> ApiResponse[SteeringOutcome]:
        """Route a hint/redirect through the steering directive.

        Wraps the operator-supplied text via :func:`wrap_untrusted` at
        the controller boundary: the agent will read this text as
        untrusted content the next time it consumes interrupts, so
        the boundary must apply the prompt-safety envelope before the
        directive persists it. The directive applies its own wrap on
        the persisted question for defence-in-depth; double-wrapping is
        safe because the safety envelope is idempotent on already-tagged
        content.

        Returns:
            ``ApiResponse[SteeringOutcome]`` instance.
        """
        app_state: AppState = state.app_state
        logger.info(
            COCKPIT_INTERVENTION_INITIATED,
            intervention_kind=kind.value,
            execution_id=data.execution_id,
            agent_id=data.agent_id,
        )
        steering = require_service(
            app_state.slice(CockpitStateSlice).steering_directive, "Steering Directive"
        )
        outcome = await steering.steer(
            kind=kind,
            execution_id=data.execution_id,
            agent_id=data.agent_id,
            details={"text": wrap_untrusted(TAG_TASK_DATA, data.text)},
        )
        logger.info(
            COCKPIT_INTERVENTION_APPLIED,
            intervention_kind=kind.value,
            execution_id=data.execution_id,
            agent_id=data.agent_id,
            applied=outcome.applied,
        )
        return ApiResponse(data=outcome)
