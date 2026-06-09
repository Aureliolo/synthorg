"""Simulation run lifecycle endpoints at /simulations."""

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Final, cast

from litestar import Controller, Request, get, post
from litestar.datastructures import State
from litestar.params import QueryParameter
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.api.channels import CHANNEL_SIMULATIONS, publish_ws_event
from synthorg.api.controllers._simulation_runtime import (
    attach_runner_callbacks,
    rollback_register_if_absent,
    run_in_background,
)
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.api.ws_models import WsEventType
from synthorg.client.models import SimulationConfig, SimulationMetrics
from synthorg.client.report.detailed import DetailedReport
from synthorg.client.report.summary import SummaryReport
from synthorg.client.state import client_simulation_state_of
from synthorg.client.store import SimulationRecord
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ConflictError, NotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.client import (
    SIMULATION_RUN_CANCELLED,
    SIMULATION_RUN_FAILED,
)

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


class StartSimulationPayload(BaseModel):
    """Request payload for starting a new simulation run."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    config: SimulationConfig = Field(description="Simulation configuration")


class SimulationStatusResponse(BaseModel):
    """Public view of a simulation run."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    simulation_id: NotBlankStr
    status: NotBlankStr
    config: SimulationConfig
    metrics: SimulationMetrics
    progress: float = Field(ge=0.0, le=1.0)
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    error: str | None = None


def _to_response(record: SimulationRecord) -> SimulationStatusResponse:
    """Convert a store record into the API response shape.

    Returns:
        ``SimulationStatusResponse`` instance.
    """
    return SimulationStatusResponse(
        simulation_id=record.simulation_id,
        status=record.status,
        config=record.config,
        metrics=record.metrics,
        progress=record.progress,
        started_at=record.started_at,
        completed_at=record.completed_at,
        error=record.error,
    )


def _publish_event(
    request: Request[object, object, State],
    event_type: WsEventType,
    record: SimulationRecord,
) -> None:
    """Best-effort publish a simulation lifecycle event."""
    publish_ws_event(
        request,
        event_type,
        CHANNEL_SIMULATIONS,
        {
            "simulation_id": record.simulation_id,
            "status": record.status,
            "progress": record.progress,
        },
    )


class SimulationController(Controller):
    """Simulation run lifecycle endpoints."""

    path = "/simulations"
    tags = ("simulations",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_simulations(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[SimulationStatusResponse]:
        """List all known simulation runs.

        Returns:
            Result matching the declared return annotation.
        """
        app_state: AppState = state.app_state
        sim_state = client_simulation_state_of(app_state)
        records = await sim_state.simulation_store.list_all()
        responses = tuple(_to_response(r) for r in records)
        page, meta = paginate_cursor(
            responses,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(state.app_state),
        )
        return PaginatedResponse(data=page, pagination=meta)

    @get("/{simulation_id:str}")
    async def get_simulation(
        self,
        state: State,
        simulation_id: PathId,
    ) -> ApiResponse[SimulationStatusResponse]:
        """Return a single simulation run record.

        Returns:
            ``ApiResponse[SimulationStatusResponse]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        sim_state = client_simulation_state_of(app_state)
        try:
            record = await sim_state.simulation_store.get(simulation_id)
        except KeyError as exc:
            msg = f"Simulation {simulation_id!r} not found"
            raise NotFoundError(msg) from exc
        return ApiResponse(data=_to_response(record))

    @post(
        "/",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("simulations.create", key="user"),
        ],
        status_code=201,
    )
    async def start_simulation(
        self,
        request: Request[object, object, State],
        state: State,
        data: StartSimulationPayload,
    ) -> ApiResponse[SimulationStatusResponse]:
        """Start a new simulation run in the background.

        The run executes asynchronously; poll ``GET /simulations/{id}``
        to observe progress and final metrics.

        Returns:
            ``ApiResponse[SimulationStatusResponse]`` instance.

        Raises:
            ConflictError: Raised on the corresponding failure path.
            MemoryError: Raised on the corresponding failure path.
            RecursionError: Raised on the corresponding failure path.
            BaseException: Raised on the corresponding failure path.
            CancelledError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        sim_state = client_simulation_state_of(app_state)
        record = SimulationRecord(
            simulation_id=data.config.simulation_id,
            config=data.config,
            status="running",
            started_at=datetime.now(UTC),
        )
        # A JetStream redelivery or HTTP 5xx retry of /simulations/start
        # with the same ``simulation_id`` would otherwise spawn a second
        # runner that races the first on
        # ``simulation_store.update_status``, corrupting metrics with
        # last-write-wins. ``register_if_absent`` performs the check
        # and insert atomically under the store's lock, so two
        # concurrent callers cannot both observe absence and proceed.
        # The losing caller gets HTTP 409 and can fall back to
        # ``GET /simulations/{id}`` to observe the in-flight run.
        if not await sim_state.simulation_store.register_if_absent(record):
            msg = (
                f"Simulation {data.config.simulation_id!r} already exists; "
                "cannot start a second runner for the same id"
            )
            raise ConflictError(msg)

        async def runner_task() -> None:
            """Run runner task.

            Raises:
                CancelledError: Raised on the corresponding failure path.
            """
            try:
                await run_in_background(app_state=app_state, record=record)
            except asyncio.CancelledError:
                logger.info(
                    SIMULATION_RUN_CANCELLED,
                    simulation_id=record.simulation_id,
                )
                raise
            except Exception as exc:
                reraise_critical(exc)
                # Drop ``logger.exception`` -- frame-locals on the
                # simulation-run-failed traceback can carry the
                # entire simulation config (matches the rationale
                # documented in ``_run_in_background``).
                logger.warning(
                    SIMULATION_RUN_FAILED,
                    simulation_id=record.simulation_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                try:
                    await sim_state.simulation_store.update_status(
                        record.simulation_id,
                        status="failed",
                        error="Simulation failed unexpectedly",
                    )
                except (ValueError, KeyError) as inner_exc:
                    logger.warning(
                        SIMULATION_RUN_FAILED,
                        simulation_id=record.simulation_id,
                        stage="final_status_write",
                        error_type=type(inner_exc).__name__,
                        error=safe_error_description(inner_exc),
                    )
            try:
                final = await sim_state.simulation_store.get(
                    record.simulation_id,
                )
            except KeyError:
                return
            event_map = {
                "completed": WsEventType.SIMULATION_COMPLETED,
                "cancelled": WsEventType.SIMULATION_CANCELLED,
                "failed": WsEventType.SIMULATION_FAILED,
            }
            event = event_map.get(final.status)
            if event is not None:
                _publish_event(request, event, final)

        # Roll back the ``register_if_absent`` claim if any post-claim
        # step (publish, runner spawn, callback registration) raises.
        # Without rollback the ``simulation_id`` would stay claimed
        # forever and block every retry, defeating the very 409-on-
        # duplicate guard the claim provides.
        spawned_task: asyncio.Task[None] | None = None
        try:
            _publish_event(request, WsEventType.SIMULATION_STARTED, record)
            spawned_task = asyncio.create_task(
                runner_task(),
                name=f"simulation-runner[{record.simulation_id}]",
            )
            attach_runner_callbacks(
                spawned_task,
                sim_state=sim_state,
                simulation_id=record.simulation_id,
            )
        except MemoryError, RecursionError:
            raise
        except BaseException as exc:
            # Log the rollback trigger before tearing down -- without
            # this entry, a failure between ``register_if_absent`` and
            # the callback wiring would leave only the rollback drain
            # log, with no record of the original cause for the start
            # that the operator would have to chase across components.
            logger.warning(
                SIMULATION_RUN_FAILED,
                simulation_id=record.simulation_id,
                stage="post_claim_setup",
                spawned_task=spawned_task is not None,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            await rollback_register_if_absent(
                spawned_task,
                sim_state=sim_state,
                record=record,
            )
            raise
        return ApiResponse(data=_to_response(record))

    @post(
        "/{simulation_id:str}/cancel",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("simulations.cancel", key="user"),
        ],
    )
    async def cancel_simulation(
        self,
        request: Request[object, object, State],
        state: State,
        simulation_id: PathId,
    ) -> ApiResponse[SimulationStatusResponse]:
        """Mark a simulation run as cancelled.

        The in-memory runner does not support cooperative
        cancellation yet, so this is a soft cancel that flips the
        status flag. Already-terminal runs produce a 409.

        Raises:
            NotFoundError: If the simulation id is not known.
            ConflictError: If the run is already in a terminal state.

        Returns:
            ``ApiResponse[SimulationStatusResponse]`` instance.
        """
        app_state: AppState = state.app_state
        sim_state = client_simulation_state_of(app_state)
        try:
            record = await sim_state.simulation_store.get(simulation_id)
        except KeyError as exc:
            msg = f"Simulation {simulation_id!r} not found"
            raise NotFoundError(msg) from exc
        if record.status in {"completed", "cancelled", "failed"}:
            msg = f"Simulation already {record.status}"
            raise ConflictError(msg)
        try:
            updated = await sim_state.simulation_store.update_status(
                simulation_id,
                status="cancelled",
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
        _publish_event(request, WsEventType.SIMULATION_CANCELLED, updated)
        return ApiResponse(data=_to_response(updated))

    @get("/{simulation_id:str}/report")
    async def get_report(
        self,
        state: State,
        simulation_id: PathId,
        fmt: Annotated[str, QueryParameter()] = "summary",
    ) -> ApiResponse[dict[str, object]]:
        """Return a generated report for a simulation run.

        Args:
            state: Injected app state.
            simulation_id: Id of the run to report on.
            fmt: Report format -- ``summary`` (default) or
                ``detailed``.

        Raises:
            NotFoundError: If the simulation id is not known.
            ConflictError: If ``fmt`` is not a supported format.

        Returns:
            ``ApiResponse[dict[str, object]]`` instance.
        """
        app_state: AppState = state.app_state
        sim_state = client_simulation_state_of(app_state)
        try:
            record = await sim_state.simulation_store.get(simulation_id)
        except KeyError as exc:
            msg = f"Simulation {simulation_id!r} not found"
            raise NotFoundError(msg) from exc
        if fmt == "summary":
            payload = await SummaryReport().generate_report(record.metrics)
        elif fmt == "detailed":
            payload = await DetailedReport().generate_report(record.metrics)
        else:
            msg = f"Unsupported report format: {fmt!r}"
            raise ConflictError(msg)
        payload["simulation_id"] = record.simulation_id
        payload["status"] = record.status
        return ApiResponse(data=cast("dict[str, object]", payload))
