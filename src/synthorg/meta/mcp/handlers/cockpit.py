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
from synthorg.core.enums import InterventionKind
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.cockpit.state import CockpitStateSlice
from synthorg.engine.intervention import SupersedeMode
from synthorg.engine.intervention.models import STEERABLE_KINDS
from synthorg.engine.state import task_engine_of
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,
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
_ARG_TASK_ID = "task_id"
_ARG_TEXT = "text"
_ARG_TURN_INDEX = "turn_index"
_ARG_PROJECT_ID = "project_id"
_ARG_KIND = "kind"
_ARG_DIRECTIVE_ID = "directive_id"
_ARG_TASK_IDS = "task_ids"
_ARG_NARROW_TASK_IDS = "narrow_task_ids"
_ARG_NARROW_AGENT_IDS = "narrow_agent_ids"
_ARG_SUPERSEDE_TASK_IDS = "supersede_task_ids"
_ARG_SUPERSEDE_MODE = "supersede_mode"
_TY_POS_INT = "positive int"
_TY_STR_ARRAY = "array of non-empty strings"
_EXPECTED_KIND: Final[str] = (
    f"one of {'/'.join(sorted(k.value for k in STEERABLE_KINDS))}"
)
_EXPECTED_SUPERSEDE_MODE: Final[str] = (
    f"one of {'/'.join(m.value for m in SupersedeMode)}"
)


def _str_tuple(arguments: dict[str, Any], key: str) -> tuple[NotBlankStr, ...]:
    """Parse an optional array-of-strings argument into a tuple.

    Returns:
        The non-empty string ids; an empty tuple when the key is absent.

    Raises:
        ArgumentValidationError: When the value is not an array of
            non-empty strings.
    """
    raw = arguments.get(key)
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ArgumentValidationError(key, _TY_STR_ARRAY)
    out: list[NotBlankStr] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ArgumentValidationError(key, _TY_STR_ARRAY)
        out.append(NotBlankStr(item))
    return tuple(out)


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


async def _steer(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Issue a project-scoped steering directive.

    Returns:
        The JSON-encoded ``SteeringIssueResult`` or an error envelope.
    """  # noqa: DOC501 -- ArgumentValidationError raised + caught in-handler
    tool_name = "synthorg_cockpit_steer"
    try:
        _reason, _actor = require_admin_guardrails(arguments, actor)
        project_id = require_arg(arguments, _ARG_PROJECT_ID, str)
        raw_kind = require_arg(arguments, _ARG_KIND, str)
        try:
            kind = InterventionKind(raw_kind)
        except ValueError as exc:
            raise ArgumentValidationError(_ARG_KIND, _EXPECTED_KIND) from exc
        if kind not in STEERABLE_KINDS:
            raise ArgumentValidationError(_ARG_KIND, _EXPECTED_KIND)
        text = require_arg(arguments, _ARG_TEXT, str)
        raw_mode = arguments.get(_ARG_SUPERSEDE_MODE, SupersedeMode.NONE.value)
        try:
            mode = SupersedeMode(raw_mode)
        except ValueError as exc:
            raise ArgumentValidationError(
                _ARG_SUPERSEDE_MODE, _EXPECTED_SUPERSEDE_MODE
            ) from exc
        steering = require_service(
            app_state.slice(CockpitStateSlice).steering_service, "Steering Service"
        )
        result = await steering.issue(
            project_id=NotBlankStr(project_id),
            kind=kind,
            text=NotBlankStr(text),
            author=NotBlankStr(require_actor_id(actor)),
            narrow_task_ids=_str_tuple(arguments, _ARG_NARROW_TASK_IDS),
            narrow_agent_ids=_str_tuple(arguments, _ARG_NARROW_AGENT_IDS),
            supersede_task_ids=_str_tuple(arguments, _ARG_SUPERSEDE_TASK_IDS),
            supersede_mode=mode,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool_name)
        return ok(result.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool_name, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool_name, exc)
        return err(exc)


async def _steer_supersede(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Confirm the obsolete-task set for a steering directive.

    Returns:
        The JSON-encoded cancelled task ids or an error envelope.
    """  # noqa: DOC501 -- ArgumentValidationError raised + caught in-handler
    tool_name = "synthorg_cockpit_steer_supersede"
    try:
        _reason, _actor = require_admin_guardrails(arguments, actor)
        project_id = require_arg(arguments, _ARG_PROJECT_ID, str)
        directive_id = require_arg(arguments, _ARG_DIRECTIVE_ID, str)
        task_ids = _str_tuple(arguments, _ARG_TASK_IDS)
        if not task_ids:
            # task_ids is required: an empty/absent set would silently
            # "supersede" zero tasks, never surfaced to the operator.
            raise ArgumentValidationError(_ARG_TASK_IDS, _TY_STR_ARRAY)
        steering = require_service(
            app_state.slice(CockpitStateSlice).steering_service, "Steering Service"
        )
        cancelled = await steering.confirm_supersession(
            project_id=NotBlankStr(project_id),
            directive_id=NotBlankStr(directive_id),
            task_ids=task_ids,
            author=NotBlankStr(require_actor_id(actor)),
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool_name)
        return ok(
            {
                "directive_id": directive_id,
                "cancelled_task_ids": [str(t) for t in cancelled],
            }
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool_name, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool_name, exc)
        return err(exc)


async def _steer_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List the active steering directives for a project.

    Returns:
        The JSON-encoded active directives or an error envelope.
    """
    tool_name = "synthorg_cockpit_steer_list"
    try:
        project_id = require_arg(arguments, _ARG_PROJECT_ID, str)
        steering = require_service(
            app_state.slice(CockpitStateSlice).steering_service, "Steering Service"
        )
        directives = await steering.list_active(project_id=NotBlankStr(project_id))
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool_name)
        return ok(dump_many(directives))
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
        "synthorg_cockpit_steer": _steer,
        "synthorg_cockpit_steer_supersede": _steer_supersede,
        "synthorg_cockpit_steer_list": _steer_list,
    },
)
