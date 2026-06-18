"""Mission-control cockpit MCP handlers.

Read handlers shim through ``app_state.cockpit_service`` and
``app_state.flight_recorder_service``; intervention handlers enforce
``require_admin_guardrails`` then route through the task engine
(pause / kill) or the steering directive (hint / redirect), mirroring
the ``/cockpit`` REST controller.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from synthorg._core.features import require_service
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.cockpit.state import CockpitStateSlice
from synthorg.engine.intervention.models import STEERABLE_KINDS
from synthorg.engine.state import task_engine_of
from synthorg.meta.mcp.domains._cockpit_args import (
    FramesArgs,
    InterveneArgs,
    SeekArgs,
    SteerArgs,
    SteerListArgs,
    SteerSupersedeArgs,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,
)
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import (
    dump_many,
    err,
    ok,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    require_actor_id,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_admin_op_executed,
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS
from synthorg.settings.state import config_resolver_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_COCKPIT_NS: Final[str] = "cockpit"
_ARG_KIND = "kind"
_ARG_TASK_IDS = "task_ids"
_TY_STR_ARRAY = "array of non-empty strings"
_EXPECTED_KIND: Final[str] = (
    f"one of {'/'.join(sorted(k.value for k in STEERABLE_KINDS))}"
)


async def _get_live_activity(
    *,
    app_state: AppState,
    arguments: dict[str, object],  # noqa: ARG001
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
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed("synthorg_cockpit_get_live_activity", exc)
        return err(exc)


async def _get_frames(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the frames."""
    try:
        frames_args = typed_args(arguments, FramesArgs)
        recorder = require_service(
            app_state.slice(CockpitStateSlice).flight_recorder_service,
            "Flight Recorder Service",
        )
        frames = await recorder.get_frames(
            frames_args.execution_id,
            limit=frames_args.limit,
            offset=frames_args.offset,
        )
        logger.info(
            MCP_HANDLER_INVOKE_SUCCESS,
            tool_name="synthorg_cockpit_get_flight_recorder_frames",
        )
        return ok(dump_many(frames))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_cockpit_get_flight_recorder_frames", exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed("synthorg_cockpit_get_flight_recorder_frames", exc)
        return err(exc)


async def _seek(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return seek."""
    try:
        seek_args = typed_args(arguments, SeekArgs)
        recorder = require_service(
            app_state.slice(CockpitStateSlice).flight_recorder_service,
            "Flight Recorder Service",
        )
        view = await recorder.seek(seek_args.execution_id, seek_args.turn_index)
        logger.info(
            MCP_HANDLER_INVOKE_SUCCESS,
            tool_name="synthorg_cockpit_seek_flight_recorder",
        )
        return ok(view.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_cockpit_seek_flight_recorder", exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed("synthorg_cockpit_seek_flight_recorder", exc)
        return err(exc)


async def _intervene_pause(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Return intervene pause."""
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        pause_args = typed_args(arguments, InterveneArgs)
        task, _from = await task_engine_of(app_state).transition_task(
            pause_args.task_id,
            TaskStatus.INTERRUPTED,
            requested_by=require_actor_id(resolved_actor),
            reason=reason,
        )
        logger.info(
            MCP_HANDLER_INVOKE_SUCCESS,
            tool_name="synthorg_cockpit_intervene_pause",
        )
        log_handler_admin_op_executed(
            "synthorg_cockpit_intervene_pause",
            reason=reason,
            actor=resolved_actor,
            target_id=str(pause_args.task_id),
        )
        return ok(task.model_dump(mode="json"))
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated("synthorg_cockpit_intervene_pause", exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_cockpit_intervene_pause", exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed("synthorg_cockpit_intervene_pause", exc)
        return err(exc)


async def _intervene_kill(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Return intervene kill."""
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        kill_args = typed_args(arguments, InterveneArgs)
        task, _prior = await task_engine_of(app_state).cancel_task(
            kill_args.task_id,
            requested_by=require_actor_id(resolved_actor),
            reason=reason,
        )
        logger.info(
            MCP_HANDLER_INVOKE_SUCCESS,
            tool_name="synthorg_cockpit_intervene_kill",
        )
        log_handler_admin_op_executed(
            "synthorg_cockpit_intervene_kill",
            reason=reason,
            actor=resolved_actor,
            target_id=str(kill_args.task_id),
        )
        return ok(task.model_dump(mode="json"))
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated("synthorg_cockpit_intervene_kill", exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_cockpit_intervene_kill", exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed("synthorg_cockpit_intervene_kill", exc)
        return err(exc)


async def _steer(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Issue a project-scoped steering directive.

    Returns:
        The JSON-encoded ``SteeringIssueResult`` or an error envelope.
    """  # noqa: DOC501 -- ArgumentValidationError raised + caught in-handler
    tool_name = "synthorg_cockpit_steer"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        steer_args = typed_args(arguments, SteerArgs)
        # The args model validates ``kind`` to the full ``InterventionKind``
        # enum; the steering surface only accepts the steerable subset, so
        # a valid-but-non-steerable kind (e.g. ``pause``) is rejected here.
        if steer_args.kind not in STEERABLE_KINDS:
            raise ArgumentValidationError(_ARG_KIND, _EXPECTED_KIND)
        steering = require_service(
            app_state.slice(CockpitStateSlice).steering_service, "Steering Service"
        )
        result = await steering.issue(
            project_id=steer_args.project_id,
            kind=steer_args.kind,
            text=steer_args.text,
            author=NotBlankStr(require_actor_id(resolved_actor)),
            narrow_task_ids=steer_args.narrow_task_ids,
            narrow_agent_ids=steer_args.narrow_agent_ids,
            supersede_task_ids=steer_args.supersede_task_ids,
            supersede_mode=steer_args.supersede_mode,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool_name)
        log_handler_admin_op_executed(
            tool_name,
            reason=reason,
            actor=resolved_actor,
            target_id=str(result.directive_id),
        )
        return ok(result.model_dump(mode="json"))
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool_name, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool_name, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool_name, exc)
        return err(exc)


async def _steer_supersede(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Confirm the obsolete-task set for a steering directive.

    Returns:
        The JSON-encoded cancelled task ids or an error envelope.
    """  # noqa: DOC501 -- ArgumentValidationError raised + caught in-handler
    tool_name = "synthorg_cockpit_steer_supersede"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        supersede_args = typed_args(arguments, SteerSupersedeArgs)
        if not supersede_args.task_ids:
            # task_ids is required: an empty set would silently
            # "supersede" zero tasks, never surfaced to the operator.
            raise ArgumentValidationError(_ARG_TASK_IDS, _TY_STR_ARRAY)
        steering = require_service(
            app_state.slice(CockpitStateSlice).steering_service, "Steering Service"
        )
        cancelled = await steering.confirm_supersession(
            project_id=supersede_args.project_id,
            directive_id=supersede_args.directive_id,
            task_ids=supersede_args.task_ids,
            author=NotBlankStr(require_actor_id(resolved_actor)),
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool_name)
        log_handler_admin_op_executed(
            tool_name,
            reason=reason,
            actor=resolved_actor,
            target_id=str(supersede_args.directive_id),
        )
        return ok(
            {
                "directive_id": supersede_args.directive_id,
                "cancelled_task_ids": [str(t) for t in cancelled],
            }
        )
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool_name, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool_name, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool_name, exc)
        return err(exc)


async def _steer_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List the active steering directives for a project.

    Returns:
        The JSON-encoded active directives or an error envelope.
    """
    tool_name = "synthorg_cockpit_steer_list"
    try:
        list_args = typed_args(arguments, SteerListArgs)
        steering = require_service(
            app_state.slice(CockpitStateSlice).steering_service, "Steering Service"
        )
        directives = await steering.list_active(project_id=list_args.project_id)
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool_name)
        return ok(dump_many(directives))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool_name, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
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
