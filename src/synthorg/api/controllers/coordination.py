"""Coordination controller -- multi-agent coordination endpoint."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from litestar import Controller, Request, post
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.channels import CHANNEL_TASKS, get_channels_plugin
from synthorg.api.dto import (
    ApiResponse,
    CoordinateTaskRequest,
    CoordinationPhaseResponse,
    CoordinationResultResponse,
)
from synthorg.api.guards import require_write_access
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.ws_models import WsEvent, WsEventType
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import (
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from synthorg.engine.coordination.models import (
    CoordinationContext,
    CoordinationResult,
)
from synthorg.engine.errors import CoordinationPhaseError
from synthorg.engine.state import EngineStateSlice
from synthorg.hr.state import HrStateSlice
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import (
    API_COORDINATION_AGENT_RESOLVE_FAILED,
    API_COORDINATION_COMPLETED,
    API_COORDINATION_FAILED,
    API_COORDINATION_STARTED,
    API_RESOURCE_NOT_FOUND,
    API_WS_SEND_FAILED,
)
from synthorg.settings.state import config_resolver_of
from synthorg.workers.state import RuntimeStateSlice

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task
    from synthorg.engine.coordination.attribution import (
        CoordinationResultWithAttribution,
    )

logger = get_logger(__name__)


def _publish_ws_event(
    request: Request[Any, Any, Any],
    event_type: WsEventType,
    payload: dict[str, object],
) -> None:
    """Best-effort publish a coordination event to the tasks channel."""
    channels_plugin = get_channels_plugin(request)
    if channels_plugin is None:
        logger.warning(
            API_WS_SEND_FAILED,
            note="ChannelsPlugin not available, dropping coordination WS event",
            event_type=event_type.value,
        )
        return

    event = WsEvent(
        event_type=event_type,
        channel=CHANNEL_TASKS,
        timestamp=datetime.now(UTC),
        payload=payload,
    )
    try:
        channels_plugin.publish(
            event.model_dump_json(),
            channels=[CHANNEL_TASKS],
        )
    except Exception as exc:
        reraise_critical(exc)
        # Drop exc_info -- channels_plugin internals can carry
        # connection metadata; surface scrubbed type+msg.
        logger.warning(
            API_WS_SEND_FAILED,
            note="Failed to publish coordination WebSocket event",
            event_type=event_type.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


def _map_result_to_response(
    result: CoordinationResult,
    *,
    currency: str = DEFAULT_CURRENCY,
) -> CoordinationResultResponse:
    """Map a domain ``CoordinationResult`` to an API response DTO.

    Returns:
        ``CoordinationResultResponse`` instance.
    """
    return CoordinationResultResponse(
        parent_task_id=result.parent_task_id,
        topology=result.topology.value,
        total_duration_seconds=result.total_duration_seconds,
        total_cost=result.total_cost,
        currency=currency,
        phases=tuple(
            CoordinationPhaseResponse(
                phase=p.phase,
                success=p.success,
                duration_seconds=p.duration_seconds,
                error=p.error,
            )
            for p in result.phases
        ),
        wave_count=len(result.waves),
    )  # is_success is @computed_field from phases


class CoordinationController(Controller):
    """Multi-agent coordination endpoint."""

    path = "/tasks/{task_id:str}/coordinate"
    tags = ("coordination",)

    @post(
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("tasks.coordinate", key="user"),
        ],
        status_code=200,
    )
    async def coordinate_task(
        self,
        request: Request[Any, Any, Any],
        state: State,
        task_id: PathId,
        data: CoordinateTaskRequest,
    ) -> ApiResponse[CoordinationResultResponse]:
        """Trigger multi-agent coordination for a task.

        Args:
            request: The incoming request.
            state: Application state.
            task_id: Task identifier.
            data: Coordination request payload.

        Returns:
            Coordination result envelope.

        Raises:
            NotFoundError: If the task is not found.
            ValidationError: If agent resolution fails.
            ServiceUnavailableError: If coordinator not configured.
        """
        app_state: AppState = state.app_state

        if app_state.slice(RuntimeStateSlice).coordinator is None:
            logger.warning(
                API_COORDINATION_FAILED,
                error="Coordinator not configured",
            )
            msg = "Coordinator not configured"
            raise ServiceUnavailableError(msg)

        if app_state.slice(HrStateSlice).agent_registry is None:
            logger.warning(
                API_COORDINATION_FAILED,
                error="Agent registry not configured",
            )
            msg = "Agent registry not configured"
            raise ServiceUnavailableError(msg)

        task = await self._get_task(app_state, task_id)
        agents = await self._resolve_agents(app_state, data, task_id)
        context = await self._build_context(app_state, task, agents, data)

        _publish_ws_event(
            request,
            WsEventType.COORDINATION_STARTED,
            {"task_id": task_id, "agent_count": len(agents)},
        )
        logger.info(
            API_COORDINATION_STARTED,
            task_id=task_id,
            agent_count=len(agents),
        )

        attributed = await self._execute(
            app_state,
            request,
            context,
            task_id,
        )
        try:
            budget_cfg = await config_resolver_of(app_state).get_budget_config()
            currency = budget_cfg.currency
        except Exception as exc:
            reraise_critical(exc)
            # Drop ``exc_info=True`` -- the config-resolver traceback
            # can carry secret-store URLs in frame-locals.
            logger.warning(
                API_COORDINATION_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="budget config unavailable, using default currency",
            )
            currency = DEFAULT_CURRENCY
        return ApiResponse(
            data=_map_result_to_response(
                attributed.result,
                currency=currency,
            ),
        )

    async def _get_task(
        self,
        app_state: AppState,
        task_id: PathId,
    ) -> Task:
        """Fetch task or raise 404.

        Returns:
            ``Task`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        task_engine = require_service(
            app_state.slice(EngineStateSlice).task_engine, "Task Engine"
        )
        task = await task_engine.get_task(task_id)
        if task is None:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="task",
                id=task_id,
            )
            msg = f"Task {task_id!r} not found"
            raise NotFoundError(msg)
        return task

    async def _build_context(
        self,
        app_state: AppState,
        task: Task,
        agents: tuple[AgentIdentity, ...],
        data: CoordinateTaskRequest,
    ) -> CoordinationContext:
        """Build coordination context from request data.

        Returns:
            ``CoordinationContext`` instance.
        """
        from synthorg.engine.decomposition.models import (  # noqa: PLC0415
            DecompositionContext,
        )

        coord_config = await config_resolver_of(app_state).get_coordination_config(
            max_concurrency_per_wave=data.max_concurrency_per_wave,
            fail_fast=data.fail_fast,
        )
        return CoordinationContext(
            task=task,
            available_agents=agents,
            decomposition_context=DecompositionContext(
                max_subtasks=data.max_subtasks,
            ),
            config=coord_config,
        )

    async def _execute(
        self,
        app_state: AppState,
        request: Request[Any, Any, Any],
        context: CoordinationContext,
        task_id: PathId,
    ) -> CoordinationResultWithAttribution:
        """Run coordination and publish WS events.

        Returns:
            ``CoordinationResultWithAttribution`` instance.

        Raises:
            ValidationError: Raised on the corresponding failure path.
            Exception: Raised on the corresponding failure path.
        """
        try:
            coordinator = require_service(
                app_state.slice(RuntimeStateSlice).coordinator, "Coordinator"
            )
            attributed = await coordinator.coordinate(context)
        except CoordinationPhaseError as exc:
            logger.warning(
                API_COORDINATION_FAILED,
                task_id=task_id,
                phase=exc.phase,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            client_msg = f"Coordination failed at phase {exc.phase!r}"
            _publish_ws_event(
                request,
                WsEventType.COORDINATION_FAILED,
                {
                    "task_id": task_id,
                    "phase": exc.phase,
                    "error": client_msg,
                },
            )
            raise ValidationError(client_msg) from exc
        except Exception as exc:
            reraise_critical(exc)
            # Drop ``logger.exception`` -- frame-locals on the
            # unexpected-coordination traceback can carry the full
            # coordination context (task body, agent rosters).
            log_exception_redacted(
                logger, API_COORDINATION_FAILED, exc, task_id=task_id
            )
            _publish_ws_event(
                request,
                WsEventType.COORDINATION_FAILED,
                {"task_id": task_id, "error": "Unexpected coordination error"},
            )
            raise

        result = attributed.result
        is_success = attributed.is_success

        ws_event_type = (
            WsEventType.COORDINATION_COMPLETED
            if is_success
            else WsEventType.COORDINATION_FAILED
        )
        _publish_ws_event(
            request,
            ws_event_type,
            {
                "task_id": task_id,
                "topology": result.topology.value,
                "is_success": is_success,
                "total_duration_seconds": result.total_duration_seconds,
            },
        )
        log_event = (
            API_COORDINATION_COMPLETED if is_success else API_COORDINATION_FAILED
        )
        log_fn = logger.info if is_success else logger.warning
        log_fn(
            log_event,
            task_id=task_id,
            topology=result.topology.value,
            is_success=is_success,
            total_duration_seconds=result.total_duration_seconds,
        )
        return attributed

    async def _resolve_agents(
        self,
        app_state: AppState,
        data: CoordinateTaskRequest,
        task_id: PathId,
    ) -> tuple[AgentIdentity, ...]:
        """Resolve agent identities from request or registry.

        Args:
            app_state: Application state.
            data: Coordination request with optional agent names.
            task_id: Task ID for logging.

        Returns:
            Tuple of agent identities.

        Raises:
            ValidationError: If agents cannot be resolved.
        """
        registry = require_service(
            app_state.slice(HrStateSlice).agent_registry, "Agent Registry"
        )

        if data.agent_names is not None:
            names = data.agent_names
            results = await registry.get_by_names(tuple(names))
            agents: list[AgentIdentity] = []
            for name, agent in zip(names, results, strict=True):
                if agent is None:
                    logger.warning(
                        API_COORDINATION_AGENT_RESOLVE_FAILED,
                        task_id=task_id,
                        agent_name=name,
                    )
                    msg = f"Agent {name!r} not found"
                    raise ValidationError(msg)
                agents.append(agent)
            return tuple(agents)

        active_agents = await registry.list_active()
        if not active_agents:
            logger.warning(
                API_COORDINATION_AGENT_RESOLVE_FAILED,
                task_id=task_id,
                error="No active agents available",
            )
            msg = "No active agents available for coordination"
            raise ValidationError(msg)
        return active_agents
