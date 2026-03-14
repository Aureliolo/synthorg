"""Coordination controller — multi-agent coordination endpoint."""

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from litestar import Controller, Request, post
from litestar.datastructures import State  # noqa: TC002

from synthorg.api.channels import CHANNEL_TASKS, get_channels_plugin
from synthorg.api.dto import (
    ApiResponse,
    CoordinateTaskRequest,
    CoordinationPhaseResponse,
    CoordinationResultResponse,
)
from synthorg.api.errors import (
    ApiValidationError,
    NotFoundError,
    ServiceUnavailableError,
)
from synthorg.api.guards import require_write_access
from synthorg.api.ws_models import WsEvent, WsEventType
from synthorg.engine.coordination.models import (
    CoordinationContext,
    CoordinationResult,
)
from synthorg.engine.errors import CoordinationPhaseError
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_COORDINATION_AGENT_RESOLVE_FAILED,
    API_COORDINATION_COMPLETED,
    API_COORDINATION_FAILED,
    API_COORDINATION_STARTED,
    API_RESOURCE_NOT_FOUND,
    API_WS_SEND_FAILED,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task

logger = get_logger(__name__)

_MAX_TASK_ID_LEN: int = 128


def _validate_task_id(task_id: str) -> None:
    """Reject oversized task IDs at the API boundary."""
    if len(task_id) > _MAX_TASK_ID_LEN:
        msg = "Task ID too long"
        raise ApiValidationError(msg)


def _publish_ws_event(
    request: Request[Any, Any, Any],
    event_type: WsEventType,
    payload: dict[str, object],
) -> None:
    """Best-effort publish a coordination event to the tasks channel."""
    channels_plugin = get_channels_plugin(request)
    if channels_plugin is None:
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
    except MemoryError, RecursionError:
        raise
    except Exception:
        logger.warning(
            API_WS_SEND_FAILED,
            note="Failed to publish coordination WebSocket event",
            event_type=event_type.value,
            exc_info=True,
        )


def _map_result_to_response(
    result: CoordinationResult,
) -> CoordinationResultResponse:
    """Map a domain ``CoordinationResult`` to an API response DTO."""
    return CoordinationResultResponse(
        parent_task_id=result.parent_task_id,
        topology=result.topology.value,
        total_duration_seconds=result.total_duration_seconds,
        total_cost_usd=result.total_cost_usd,
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

    @post(guards=[require_write_access], status_code=200)
    async def coordinate_task(
        self,
        request: Request[Any, Any, Any],
        state: State,
        task_id: str,
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
            ApiValidationError: If agent resolution fails.
            ServiceUnavailableError: If coordinator not configured.
        """
        app_state: AppState = state.app_state

        if not app_state.has_coordinator:
            logger.warning(
                API_COORDINATION_FAILED,
                error="Coordinator not configured",
            )
            msg = "Coordinator not configured"
            raise ServiceUnavailableError(msg)

        if not app_state.has_agent_registry:
            logger.warning(
                API_COORDINATION_FAILED,
                error="Agent registry not configured",
            )
            msg = "Agent registry not configured"
            raise ServiceUnavailableError(msg)

        _validate_task_id(task_id)
        task = await self._get_task(app_state, task_id)
        agents = await self._resolve_agents(app_state, data, task_id)
        context = self._build_context(app_state, task, agents, data)

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

        result = await self._execute(
            app_state,
            request,
            context,
            task_id,
        )
        return ApiResponse(data=_map_result_to_response(result))

    async def _get_task(
        self,
        app_state: AppState,
        task_id: str,
    ) -> Task:
        """Fetch task or raise 404."""
        task = await app_state.task_engine.get_task(task_id)
        if task is None:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="task",
                id=task_id,
            )
            msg = f"Task {task_id!r} not found"
            raise NotFoundError(msg)
        return task

    def _build_context(
        self,
        app_state: AppState,
        task: Task,
        agents: tuple[AgentIdentity, ...],
        data: CoordinateTaskRequest,
    ) -> CoordinationContext:
        """Build coordination context from request data."""
        from synthorg.engine.decomposition.models import (  # noqa: PLC0415
            DecompositionContext,
        )

        coord_config = app_state.config.coordination.to_coordination_config(
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
        task_id: str,
    ) -> CoordinationResult:
        """Run coordination and publish WS events."""
        try:
            result = await app_state.coordinator.coordinate(context)
        except CoordinationPhaseError as exc:
            logger.warning(
                API_COORDINATION_FAILED,
                task_id=task_id,
                phase=exc.phase,
                error=str(exc),
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
            raise ApiValidationError(client_msg) from exc
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                API_COORDINATION_FAILED,
                task_id=task_id,
                error="Unexpected exception during coordination",
            )
            _publish_ws_event(
                request,
                WsEventType.COORDINATION_FAILED,
                {"task_id": task_id, "error": "Unexpected coordination error"},
            )
            raise

        ws_event_type = (
            WsEventType.COORDINATION_COMPLETED
            if result.is_success
            else WsEventType.COORDINATION_FAILED
        )
        _publish_ws_event(
            request,
            ws_event_type,
            {
                "task_id": task_id,
                "topology": result.topology.value,
                "is_success": result.is_success,
                "total_duration_seconds": result.total_duration_seconds,
            },
        )
        log_event = (
            API_COORDINATION_COMPLETED if result.is_success else API_COORDINATION_FAILED
        )
        log_fn = logger.info if result.is_success else logger.warning
        log_fn(
            log_event,
            task_id=task_id,
            topology=result.topology.value,
            is_success=result.is_success,
            total_duration_seconds=result.total_duration_seconds,
        )
        return result

    async def _resolve_agents(
        self,
        app_state: AppState,
        data: CoordinateTaskRequest,
        task_id: str,
    ) -> tuple[AgentIdentity, ...]:
        """Resolve agent identities from request or registry.

        Args:
            app_state: Application state.
            data: Coordination request with optional agent names.
            task_id: Task ID for logging.

        Returns:
            Tuple of agent identities.

        Raises:
            ApiValidationError: If agents cannot be resolved.
        """
        registry = app_state.agent_registry

        if data.agent_names is not None:
            results = await asyncio.gather(
                *(registry.get_by_name(name) for name in data.agent_names)
            )
            agents: list[AgentIdentity] = []
            for name, agent in zip(data.agent_names, results, strict=True):
                if agent is None:
                    logger.warning(
                        API_COORDINATION_AGENT_RESOLVE_FAILED,
                        task_id=task_id,
                        agent_name=name,
                    )
                    msg = f"Agent {name!r} not found"
                    raise ApiValidationError(msg)
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
            raise ApiValidationError(msg)
        return active_agents
