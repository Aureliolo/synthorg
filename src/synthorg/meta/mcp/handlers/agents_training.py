"""Personality + training MCP handlers.

Shims the personality registry reads and the training-session
list / get / start tools onto the HR personality and training services.
Each handler degrades to a ``capability_gap`` envelope when its service
is not wired on ``app_state``.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import ValidationError

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.hr.errors import (
    PersonalityNotFoundError,
    TrainingSessionNotFoundError,
)
from synthorg.hr.seniority import SeniorityLevel
from synthorg.hr.state import (
    HrStateSlice,
    personality_service_of,
    training_service_of,
)
from synthorg.hr.training.models import ContentType, TrainingPlan
from synthorg.meta.mcp.domains._agents_args import (
    PersonalitiesGetArgs,
    PersonalitiesListArgs,
    TrainingGetSessionArgs,
    TrainingListSessionsArgs,
    TrainingStartSessionArgs,
)
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    capability_gap,
    dump_many,
    err,
    ok,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_WHY_PERSONALITIES = (
    "personality registry is not exposed on app_state; personalities "
    "are stored on AgentIdentity.personality"
)
_WHY_TRAINING_LIST = (
    "training_service.execute() is the only public entry point; "
    "list/get session metadata is not materialised"
)
_WHY_TRAINING_START = (
    "training_service.execute() requires a TrainingPlan -- not "
    "representable in the current MCP tool schema"
)


async def _personalities_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_personalities_list`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_personalities_list"
    try:
        page = typed_args(arguments, PersonalitiesListArgs)
        offset, limit = page.offset, page.limit
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    if app_state.slice(HrStateSlice).personality_service is None:
        return capability_gap(tool, _WHY_PERSONALITIES)
    try:
        entries, total = await personality_service_of(app_state).list_personalities(
            offset=offset,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(entries), pagination=meta)


async def _personalities_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_personalities_get`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_personalities_get"
    try:
        name = typed_args(arguments, PersonalitiesGetArgs).name
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    if app_state.slice(HrStateSlice).personality_service is None:
        return capability_gap(tool, _WHY_PERSONALITIES)
    try:
        entry = await personality_service_of(app_state).get_personality(
            NotBlankStr(name),
        )
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if entry is None:
        missing = PersonalityNotFoundError(f"Personality {name!r} not found")
        log_handler_invoke_failed(tool, missing)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=entry.model_dump(mode="json"))


async def _training_list_sessions(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_training_list_sessions`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_training_list_sessions"
    try:
        page = typed_args(arguments, TrainingListSessionsArgs)
        offset, limit = page.offset, page.limit
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    if app_state.slice(HrStateSlice).training_service is None:
        return capability_gap(tool, _WHY_TRAINING_LIST)
    try:
        sessions, total = await training_service_of(app_state).list_sessions(
            offset=offset,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(sessions), pagination=meta)


async def _training_get_session(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_training_get_session`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_training_get_session"
    try:
        plan_id = typed_args(arguments, TrainingGetSessionArgs).session_id
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    if app_state.slice(HrStateSlice).training_service is None:
        return capability_gap(tool, _WHY_TRAINING_LIST)
    try:
        session = await training_service_of(app_state).get_session(
            NotBlankStr(plan_id),
        )
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if session is None:
        missing = TrainingSessionNotFoundError(
            f"Training session {plan_id!r} not found",
        )
        log_handler_invoke_failed(tool, missing)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=session.model_dump(mode="json"))


async def _training_start_session(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_training_start_session`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_training_start_session"
    try:
        plan = _build_training_plan(typed_args(arguments, TrainingStartSessionArgs))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    if app_state.slice(HrStateSlice).training_service is None:
        return capability_gap(tool, _WHY_TRAINING_START)
    try:
        result = await training_service_of(app_state).start_session(plan)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=result.model_dump(mode="json"))


def _build_training_plan(args: TrainingStartSessionArgs) -> TrainingPlan:
    """Construct a :class:`TrainingPlan` from validated MCP args.

    The MCP tool only surfaces the fields a caller needs to launch a
    fresh training session; richer fields (volume caps, custom
    selectors) stay at their :class:`TrainingPlan` defaults. The
    closed-enum ``new_agent_level`` / ``enabled_content_types`` Literals
    are mapped onto their domain enums.

    Returns:
        ``TrainingPlan`` instance.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    arg_plan = "plan"
    enabled = (
        frozenset(ContentType(v) for v in args.enabled_content_types)
        if args.enabled_content_types
        else frozenset(ContentType)
    )
    try:
        return TrainingPlan(
            new_agent_id=args.new_agent_id,
            new_agent_role=args.new_agent_role,
            new_agent_level=SeniorityLevel(args.new_agent_level),
            new_agent_department=args.new_agent_department,
            enabled_content_types=enabled,
            created_at=datetime.now(UTC),
        )
    except ValidationError as exc:
        expected_plan = f"valid TrainingPlan shape ({len(exc.errors())} error(s))"
        raise ArgumentValidationError(arg_plan, expected_plan) from exc
