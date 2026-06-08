"""Mission-control cockpit controller: live activity, flight recorder, intervention.

Live activity and flight-recorder reads are read-access; interventions
require write access. All endpoints 503 (via the ``AppState`` service
properties) until the cockpit services are wired after persistence
connects. Interventions are audit-logged via ``cockpit.intervention.*``.
"""

from typing import Annotated, Final

from litestar import Controller, get, post
from litestar.datastructures import State
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
from synthorg.api.path_params import PathId
from synthorg.api.state import AppState
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.cockpit import LiveActivitySnapshot
from synthorg.engine.cockpit.state import CockpitStateSlice
from synthorg.engine.flight_recording import ReplaySeekView
from synthorg.engine.intervention.enums import InterventionKind
from synthorg.engine.state import EngineStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.cockpit import (
    COCKPIT_INTERVENTION_APPLIED,
    COCKPIT_INTERVENTION_INITIATED,
)
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrame,
)
from synthorg.security.redteam.models import RedTeamReportRecord
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

    @get("/flight-recorder/{execution_id:str}/red-team")
    async def get_red_team_report(
        self,
        state: State,
        execution_id: PathId,
    ) -> ApiResponse[RedTeamReportRecord | None]:
        """Return the durable red-team verdict recorded for a run, if any.

        ``data`` is ``null`` when no red-team gate ran for the execution
        (or the archive is unwired); the dashboard renders that as "no
        red-team review recorded" rather than an error.

        Returns:
            ``ApiResponse[RedTeamReportRecord | None]`` instance.
        """
        app_state: AppState = state.app_state
        recorder = require_service(
            app_state.slice(CockpitStateSlice).flight_recorder_service,
            "Flight Recorder Service",
        )
        record = await recorder.get_red_team_report(execution_id)
        return ApiResponse(data=record)

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
