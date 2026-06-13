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
from synthorg.meta.mcp.handlers.common_args import (
    require_non_blank,
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


# lint-allow: handler-arguments-get -- cataloged mismatch: handler builds a
# TrainingPlan via the custom _parse_training_plan(arguments); TrainingStartSessionArgs
# captures the inputs but needs an args-model -> TrainingPlan adapter to migrate.
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
        plan = _parse_training_plan(arguments)
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


def _parse_training_plan(arguments: dict[str, object]) -> TrainingPlan:
    """Construct a :class:`TrainingPlan` from MCP arguments.

    The MCP tool only surfaces the fields a caller needs to launch a
    fresh training session; richer fields (volume caps, custom
    selectors) stay at their :class:`TrainingPlan` defaults.

    Returns:
        ``TrainingPlan`` instance.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    arg_level = "new_agent_level"
    arg_enabled = "enabled_content_types"
    arg_plan = "plan"
    expected_level = "one of junior/mid/senior"
    expected_enabled_list = "list of content type strings"
    expected_enabled_values = (
        "list of content type strings (procedural/semantic/tool_patterns)"
    )
    new_agent_id = require_non_blank(arguments, "new_agent_id")
    new_agent_role = require_non_blank(arguments, "new_agent_role")
    raw_level = require_non_blank(arguments, arg_level)
    try:
        level = SeniorityLevel(raw_level)
    except ValueError as exc:
        raise ArgumentValidationError(arg_level, expected_level) from exc
    department: NotBlankStr | None = None
    arg_department = "new_agent_department"
    expected_department = "non-blank string"
    if arg_department in arguments:
        department_raw = arguments[arg_department]
        if department_raw is not None:
            # Reject present-but-malformed values (e.g. ``""`` or a
            # non-string); silently dropping them would change the
            # plan the caller intended to submit.
            if not isinstance(department_raw, str) or not department_raw.strip():
                raise ArgumentValidationError(arg_department, expected_department)
            department = NotBlankStr(department_raw.strip())
    enabled_raw = arguments.get("enabled_content_types")
    if enabled_raw is None:
        enabled = frozenset(ContentType)
    else:
        if not isinstance(enabled_raw, (list, tuple)):
            raise ArgumentValidationError(arg_enabled, expected_enabled_list)
        try:
            enabled = frozenset(ContentType(v) for v in enabled_raw)
        except ValueError as exc:
            raise ArgumentValidationError(arg_enabled, expected_enabled_values) from exc
    try:
        return TrainingPlan(
            new_agent_id=NotBlankStr(new_agent_id),
            new_agent_role=NotBlankStr(new_agent_role),
            new_agent_level=level,
            new_agent_department=department,
            enabled_content_types=enabled,
            created_at=datetime.now(UTC),
        )
    except ValidationError as exc:
        expected_plan = f"valid TrainingPlan shape ({len(exc.errors())} error(s))"
        raise ArgumentValidationError(arg_plan, expected_plan) from exc
