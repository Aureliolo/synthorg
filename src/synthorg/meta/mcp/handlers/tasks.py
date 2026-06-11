"""Task domain MCP handlers.

Shims the 8 task tools onto ``task_engine_of(app_state)``
(:class:`synthorg.engine.task_engine.TaskEngine`).  ``delete`` and
``cancel`` are destructive and enforce the standard
``confirm=True`` + non-blank ``reason`` + non-``None`` ``actor`` triple.
``activities_list`` has no dedicated service method; it returns a
``capability_gap`` envelope.
"""

import copy
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from pydantic import ValidationError

from synthorg.core.agent import (
    AgentIdentity,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.errors import (
    TaskMutationError,
    TaskNotFoundError,
)
from synthorg.engine.state import task_engine_of
from synthorg.hr.state import HrStateSlice, activity_feed_service_of
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,
)
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    capability_gap,
    dump_many,
    err,
    ok,
    paginate_sequence,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    actor_id,
    coerce_pagination,
    require_actor_id,
    require_arg,
    require_non_blank,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_ADMIN_OP_EXECUTED,
    MCP_HANDLER_INVOKE_SUCCESS,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


_TY_NON_BLANK = "non-blank string"
_TY_AGENT = "identified agent"
_TY_TASK_STATUS = "TaskStatus"
_ARG_TASK_ID = "task_id"
_ARG_TARGET = "target_status"
_ARG_UPDATES = "updates"
_ARG_STATUS = "status"
_ARG_ASSIGNED_TO = "assigned_to"
_ARG_PROJECT = "project"
_ARG_ACTOR = "actor"


def _coerce_status(
    raw: object,
    *,
    arg_name: str = _ARG_STATUS,
) -> TaskStatus | None:
    """Coerce a string to ``TaskStatus`` or raise ``ArgumentValidationError``.

    ``arg_name`` controls which argument the envelope blames so callers
    parsing ``status`` vs ``target_status`` get accurate feedback
    instead of every validation failure pointing at ``"status"``.

    Returns:
        The ``TaskStatus`` value when present, ``None`` otherwise.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ArgumentValidationError(arg_name, _TY_NON_BLANK)
    try:
        return TaskStatus(raw)
    except ValueError as exc:
        raise ArgumentValidationError(arg_name, _TY_TASK_STATUS) from exc


# --- handlers -------------------------------------------------------------


async def _tasks_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_tasks_list`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    tool = "synthorg_tasks_list"
    try:
        status = _coerce_status(arguments.get("status"))
        assigned_to = arguments.get("assigned_to")
        project = arguments.get("project")
        if assigned_to is not None and not isinstance(assigned_to, str):
            raise ArgumentValidationError(_ARG_ASSIGNED_TO, _TY_NON_BLANK)
        if project is not None and not isinstance(project, str):
            raise ArgumentValidationError(_ARG_PROJECT, _TY_NON_BLANK)
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    try:
        tasks, total = await task_engine_of(app_state).list_tasks(
            status=status,
            assigned_to=assigned_to,
            project=project,
        )
        page, meta = paginate_sequence(
            tasks,
            offset=offset,
            limit=limit,
            total=total,
        )
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(page), pagination=meta)


async def _tasks_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_tasks_get`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_tasks_get"
    try:
        task_id = require_non_blank(arguments, _ARG_TASK_ID)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        task = await task_engine_of(app_state).get_task(task_id)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if task is None:
        missing = TaskNotFoundError(f"Task {task_id!r} not found")
        log_handler_invoke_failed(tool, missing)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=task.model_dump(mode="json"))


async def _tasks_create(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_tasks_create`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_tasks_create"
    try:
        task_data = require_arg(arguments, "task_data", dict)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    # Validate full CreateTaskData via Pydantic so callers get a precise
    # error envelope instead of a runtime TaskMutationError on missing
    # / mistyped fields.  Local import keeps the meta handlers from
    # eagerly pulling the engine module on every import.
    from synthorg.engine.task_engine_models import (  # noqa: PLC0415
        CreateTaskData,
    )

    try:
        data = CreateTaskData.model_validate(task_data)
    except ValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc, domain_code="invalid_argument")

    requested_by = actor_id(actor) or "system"
    try:
        task = await task_engine_of(app_state).create_task(
            data,
            requested_by=requested_by,
        )
    except TaskMutationError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=task.model_dump(mode="json"))


async def _tasks_update(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_tasks_update`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    tool = "synthorg_tasks_update"
    try:
        requested_by = actor_id(actor)
        if requested_by is None:
            raise ArgumentValidationError(_ARG_ACTOR, _TY_AGENT)
        task_id = require_non_blank(arguments, _ARG_TASK_ID)
        updates = require_arg(arguments, _ARG_UPDATES, dict)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    try:
        task = await task_engine_of(app_state).update_task(
            task_id,
            updates,
            requested_by=requested_by,
        )
    except TaskNotFoundError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except TaskMutationError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=task.model_dump(mode="json"))


async def _tasks_delete(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_tasks_delete`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_tasks_delete"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        task_id = require_non_blank(arguments, _ARG_TASK_ID)
        requested_by = require_actor_id(resolved_actor)
        await task_engine_of(app_state).delete_task(
            task_id,
            requested_by=requested_by,
        )
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except TaskNotFoundError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except TaskMutationError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)

    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_ADMIN_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=requested_by,
        reason=reason,
        target_id=task_id,
    )
    return ok()


async def _tasks_transition(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_tasks_transition`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    tool = "synthorg_tasks_transition"
    try:
        requested_by = actor_id(actor)
        if requested_by is None:
            raise ArgumentValidationError(_ARG_ACTOR, _TY_AGENT)
        task_id = require_non_blank(arguments, _ARG_TASK_ID)
        target_raw = require_non_blank(arguments, _ARG_TARGET)
        target = _coerce_status(target_raw, arg_name=_ARG_TARGET)
        if target is None:
            raise ArgumentValidationError(_ARG_TARGET, _TY_TASK_STATUS)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    try:
        task, _previous = await task_engine_of(app_state).transition_task(
            task_id,
            target,
            requested_by=requested_by,
        )
    except TaskNotFoundError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except TaskMutationError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=task.model_dump(mode="json"))


async def _tasks_cancel(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_tasks_cancel`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_tasks_cancel"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        task_id = require_non_blank(arguments, _ARG_TASK_ID)
        requested_by = require_actor_id(resolved_actor)
        task, _prior_status = await task_engine_of(app_state).cancel_task(
            task_id,
            requested_by=requested_by,
            reason=reason,
        )
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except TaskNotFoundError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except TaskMutationError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)

    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_ADMIN_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=requested_by,
        reason=reason,
        target_id=task_id,
    )
    return ok(data=task.model_dump(mode="json"))


_WHY_ACTIVITY = (
    "activity feed is assembled in hr.activity module but the "
    "ActivityFeedService is not wired on app_state in this deployment"
)


def _parse_activities_args(
    arguments: dict[str, object],
) -> tuple[int, int, str | None, str | None, int | None]:
    """Validate and extract ``synthorg_activities_list`` arguments.

    Extracted from ``_activities_list`` to keep that handler under the
    ruff complexity ceiling. Returns
    ``(offset, limit, project, task_id, window_hours)`` with strings
    already trimmed and ``window_hours`` set to ``None`` when the
    caller wants the service's default window.

    Returns:
        The ``tuple[int, int, str, str, int]`` value when present, ``None`` otherwise.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    arg_project = "project"
    arg_task_id = "task_id"
    arg_window_hours = "window_hours"
    ty_pos_int = "positive int"
    offset, limit = coerce_pagination(arguments)
    project_raw = arguments.get(arg_project)
    task_id_raw = arguments.get(arg_task_id)
    if project_raw is not None and (
        not isinstance(project_raw, str) or not project_raw.strip()
    ):
        raise ArgumentValidationError(arg_project, _TY_NON_BLANK)
    if task_id_raw is not None and (
        not isinstance(task_id_raw, str) or not task_id_raw.strip()
    ):
        raise ArgumentValidationError(arg_task_id, _TY_NON_BLANK)
    window_hours_raw = arguments.get(arg_window_hours)
    window_hours: int | None = None
    if window_hours_raw is not None:
        if isinstance(window_hours_raw, bool) or not isinstance(window_hours_raw, int):
            raise ArgumentValidationError(arg_window_hours, ty_pos_int)
        if window_hours_raw < 1:
            raise ArgumentValidationError(arg_window_hours, ty_pos_int)
        window_hours = window_hours_raw
    project = project_raw.strip() if isinstance(project_raw, str) else None
    task_id = task_id_raw.strip() if isinstance(task_id_raw, str) else None
    return offset, limit, project, task_id, window_hours


async def _activities_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_activities_list`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_activities_list"
    try:
        offset, limit, project, task_id, window_hours = _parse_activities_args(
            arguments,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    if app_state.slice(HrStateSlice).activity_feed_service is None:
        return capability_gap(tool, _WHY_ACTIVITY)
    feed = activity_feed_service_of(app_state)
    try:
        if window_hours is not None:
            events, total = await feed.list_recent_activity(
                project=project,
                task_id=task_id,
                offset=offset,
                limit=limit,
                window_hours=window_hours,
            )
        else:
            events, total = await feed.list_recent_activity(
                project=project,
                task_id=task_id,
                offset=offset,
                limit=limit,
            )
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(events), pagination=meta)


TASK_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    copy.deepcopy(
        {
            "synthorg_tasks_list": _tasks_list,
            "synthorg_tasks_get": _tasks_get,
            "synthorg_tasks_create": _tasks_create,
            "synthorg_tasks_update": _tasks_update,
            "synthorg_tasks_delete": _tasks_delete,
            "synthorg_tasks_transition": _tasks_transition,
            "synthorg_tasks_cancel": _tasks_cancel,
            "synthorg_activities_list": _activities_list,
        },
    ),
)
