"""Mission-control cockpit MCP handlers.

Read handlers shim through ``app_state.cockpit_service`` and
``app_state.flight_recorder_service``; intervention handlers enforce
``require_admin_guardrails`` then route through the task engine
(pause / kill) or the steering directive (hint / redirect), mirroring
the ``/cockpit`` REST controller.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from synthorg._core.features import require_service
from synthorg.core.enums import InterventionKind, TaskStatus
from synthorg.engine.cockpit.state import CockpitStateSlice
from synthorg.engine.state import task_engine_of
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,  # noqa: TC001 -- PEP 649 annotation
)
from synthorg.meta.mcp.handlers.common import (
    dump_many,
    err,
    ok,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    coerce_pagination,
    require_actor_id,
    require_arg,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS
from synthorg.settings.state import config_resolver_of

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)

_COCKPIT_NS: Final[str] = "cockpit"
_ARG_EXECUTION_ID = "execution_id"
_ARG_AGENT_ID = "agent_id"
_ARG_TASK_ID = "task_id"
_ARG_TEXT = "text"
_ARG_TURN_INDEX = "turn_index"
_TY_POS_INT = "positive int"


def _parse_turn_index(arguments: dict[str, Any]) -> int:
    """Return parse turn index.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    raw = arguments.get(_ARG_TURN_INDEX)
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise ArgumentValidationError(_ARG_TURN_INDEX, _TY_POS_INT)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ArgumentValidationError(_ARG_TURN_INDEX, _TY_POS_INT) from exc
    if value < 1:
        raise ArgumentValidationError(_ARG_TURN_INDEX, _TY_POS_INT)
    return value


async def _get_live_activity(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the live activity."""
    try:
        resolver = config_resolver_of(app_state)
        stuck = await resolver.get_float(_COCKPIT_NS, "stuck_idle_threshold_minutes")
        runaway = await resolver.get_float(
            _COCKPIT_NS, "runaway_cost_threshold_percent"
        )
        cockpit = require_service(
            app_state.slice(CockpitStateSlice).cockpit_service, "Cockpit Service"
        )
        snapshot = await cockpit.get_live_snapshot(
            stuck_idle_minutes=stuck,
            runaway_cost_percent=runaway,
        )
        logger.info(
            MCP_HANDLER_INVOKE_SUCCESS,
            tool_name="synthorg_cockpit_get_live_activity",
        )
        return ok(snapshot.model_dump(mode="json"))
    except Exception as exc:
        log_handler_invoke_failed("synthorg_cockpit_get_live_activity", exc)
        return err(exc)


async def _get_frames(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the frames."""
    try:
        execution_id = require_arg(arguments, _ARG_EXECUTION_ID, str)
        offset, limit = coerce_pagination(arguments)
        recorder = require_service(
            app_state.slice(CockpitStateSlice).flight_recorder_service,
            "Flight Recorder Service",
        )
        frames = await recorder.get_frames(
            execution_id,
            limit=limit,
            offset=offset,
        )
        logger.info(
            MCP_HANDLER_INVOKE_SUCCESS,
            tool_name="synthorg_cockpit_get_flight_recorder_frames",
        )
        return ok(dump_many(frames))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_cockpit_get_flight_recorder_frames", exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed("synthorg_cockpit_get_flight_recorder_frames", exc)
        return err(exc)


async def _seek(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return seek."""
    try:
        execution_id = require_arg(arguments, _ARG_EXECUTION_ID, str)
        turn_index = _parse_turn_index(arguments)
        recorder = require_service(
            app_state.slice(CockpitStateSlice).flight_recorder_service,
            "Flight Recorder Service",
        )
        view = await recorder.seek(execution_id, turn_index)
        logger.info(
            MCP_HANDLER_INVOKE_SUCCESS,
            tool_name="synthorg_cockpit_seek_flight_recorder",
        )
        return ok(view.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_cockpit_seek_flight_recorder", exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed("synthorg_cockpit_seek_flight_recorder", exc)
        return err(exc)


async def _intervene_pause(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Return intervene pause."""
    try:
        reason, _actor = require_admin_guardrails(arguments, actor)
        task_id = require_arg(arguments, _ARG_TASK_ID, str)
        task, _from = await task_engine_of(app_state).transition_task(
            task_id,
            TaskStatus.INTERRUPTED,
            requested_by=require_actor_id(actor),
            reason=reason,
        )
        logger.info(
            MCP_HANDLER_INVOKE_SUCCESS,
            tool_name="synthorg_cockpit_intervene_pause",
        )
        return ok(task.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_cockpit_intervene_pause", exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed("synthorg_cockpit_intervene_pause", exc)
        return err(exc)


async def _intervene_kill(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Return intervene kill."""
    try:
        reason, _actor = require_admin_guardrails(arguments, actor)
        task_id = require_arg(arguments, _ARG_TASK_ID, str)
        task, _prior = await task_engine_of(app_state).cancel_task(
            task_id,
            requested_by=require_actor_id(actor),
            reason=reason,
        )
        logger.info(
            MCP_HANDLER_INVOKE_SUCCESS,
            tool_name="synthorg_cockpit_intervene_kill",
        )
        return ok(task.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_cockpit_intervene_kill", exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed("synthorg_cockpit_intervene_kill", exc)
        return err(exc)


async def _steer_to(
    app_state: Any,
    arguments: dict[str, Any],
    kind: InterventionKind,
    tool_name: str,
) -> str:
    """Resolve steering args and route through the steering directive.

    Returns:
        Resulting string.
    """
    execution_id = require_arg(arguments, _ARG_EXECUTION_ID, str)
    agent_id = require_arg(arguments, _ARG_AGENT_ID, str)
    text = require_arg(arguments, _ARG_TEXT, str)
    steering = require_service(
        app_state.slice(CockpitStateSlice).steering_directive, "Steering Directive"
    )
    outcome = await steering.steer(
        kind=kind,
        execution_id=execution_id,
        agent_id=agent_id,
        details={"text": text},
    )
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool_name)
    return ok(outcome.model_dump(mode="json"))


async def _intervene_hint(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Return intervene hint."""
    tool_name = "synthorg_cockpit_intervene_hint"
    try:
        require_admin_guardrails(arguments, actor)
        return await _steer_to(app_state, arguments, InterventionKind.HINT, tool_name)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool_name, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool_name, exc)
        return err(exc)


async def _intervene_redirect(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Return intervene redirect."""
    tool_name = "synthorg_cockpit_intervene_redirect"
    try:
        require_admin_guardrails(arguments, actor)
        return await _steer_to(
            app_state, arguments, InterventionKind.REDIRECT, tool_name
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool_name, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool_name, exc)
        return err(exc)


COCKPIT_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_cockpit_get_live_activity": _get_live_activity,
        "synthorg_cockpit_get_flight_recorder_frames": _get_frames,
        "synthorg_cockpit_seek_flight_recorder": _seek,
        "synthorg_cockpit_intervene_pause": _intervene_pause,
        "synthorg_cockpit_intervene_kill": _intervene_kill,
        "synthorg_cockpit_intervene_hint": _intervene_hint,
        "synthorg_cockpit_intervene_redirect": _intervene_redirect,
    },
)
