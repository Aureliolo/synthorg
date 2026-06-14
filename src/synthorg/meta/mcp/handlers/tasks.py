"""Task domain MCP handlers.

Shims the 8 task tools onto ``task_engine_of(app_state)``
(:class:`synthorg.engine.task_engine.TaskEngine`).  ``delete`` and
``cancel`` are destructive and enforce the standard
``confirm=True`` + non-blank ``reason`` + non-``None`` ``actor`` triple.
``activities_list`` has no dedicated service method; it returns a
``capability_gap`` envelope.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from pydantic import ValidationError

from synthorg.core.agent import (
    AgentIdentity,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.errors import (
    TaskMutationError,
    TaskNotFoundError,
)
from synthorg.engine.state import task_engine_of
from synthorg.hr.state import HrStateSlice, activity_feed_service_of
from synthorg.meta.mcp.domains._tasks_args import (
    ActivitiesListArgs,
    TasksCancelArgs,
    TasksCreateArgs,
    TasksDeleteArgs,
    TasksGetArgs,
    TasksListArgs,
    TasksTransitionArgs,
    TasksUpdateArgs,
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
    require_actor_id,
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


_TY_AGENT = "identified agent"
_ARG_ACTOR = "actor"


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
    """
    tool = "synthorg_tasks_list"
    try:
        args = typed_args(arguments, TasksListArgs)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    try:
        tasks, total = await task_engine_of(app_state).list_tasks(
            status=args.status,
            assigned_to=args.assigned_to,
            project=args.project,
        )
        offset, limit = args.offset, args.limit
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
        task_id = typed_args(arguments, TasksGetArgs).task_id
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
        task_data = typed_args(arguments, TasksCreateArgs).task_data
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
        update_args = typed_args(arguments, TasksUpdateArgs)
        task_id = update_args.task_id
        updates = update_args.updates
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
        task_id = typed_args(arguments, TasksDeleteArgs).task_id
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
        transition_args = typed_args(arguments, TasksTransitionArgs)
        task_id = transition_args.task_id
        target = transition_args.target_status
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
        task_id = typed_args(arguments, TasksCancelArgs).task_id
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
        args = typed_args(arguments, ActivitiesListArgs)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    if app_state.slice(HrStateSlice).activity_feed_service is None:
        return capability_gap(tool, _WHY_ACTIVITY)
    feed = activity_feed_service_of(app_state)
    offset, limit = args.offset, args.limit
    try:
        if args.window_hours is not None:
            events, total = await feed.list_recent_activity(
                project=args.project,
                task_id=args.task_id,
                offset=offset,
                limit=limit,
                window_hours=args.window_hours,
            )
        else:
            events, total = await feed.list_recent_activity(
                project=args.project,
                task_id=args.task_id,
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
)
