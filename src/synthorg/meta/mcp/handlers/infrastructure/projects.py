"""Project MCP handlers (infrastructure sub-domain)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import UUID

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import NotFoundError
from synthorg.infrastructure.state import project_facade_service_of
from synthorg.meta.mcp.domains._remaining_args import (
    ProjectsCreateArgs,
    ProjectsDeleteArgs,
    ProjectsGetArgs,
    ProjectsListArgs,
    ProjectsUpdateArgs,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    err,
    ok,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
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

_ARG_PROJECT_ID = "project_id"
_TY_UUID = "UUID string"


async def _projects_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List projects (paginated).

    Returns:
        Resulting string.
    """
    tool = "synthorg_projects_list"
    try:
        page_args = typed_args(arguments, ProjectsListArgs)
        offset, limit = page_args.offset, page_args.limit
        page, total = await project_facade_service_of(app_state).list_projects(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok([p.to_dict() for p in page], pagination=pagination)


async def _projects_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single project by ID.

    Returns:
        Resulting string.

    Raises:
        ArgumentValidationError: When ``project_id`` is not a UUID string.
    """
    tool = "synthorg_projects_get"
    try:
        project_id = typed_args(arguments, ProjectsGetArgs).project_id
        try:
            UUID(project_id)
        except ValueError as uuid_exc:
            raise ArgumentValidationError(_ARG_PROJECT_ID, _TY_UUID) from uuid_exc
        project = await project_facade_service_of(app_state).get_project(project_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if project is None:
        missing = NotFoundError(f"Project {project_id} not found")
        log_handler_invoke_failed(tool, missing, project_id=project_id)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(project.to_dict())


async def _projects_create(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Create a new project (non-destructive write).

    Returns:
        Resulting string.
    """
    tool = "synthorg_projects_create"
    try:
        args = typed_args(arguments, ProjectsCreateArgs)
        project = await project_facade_service_of(app_state).create_project(
            name=args.name,
            description=args.description,
            actor_id=require_actor_id(actor),
            metadata=args.metadata,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(project.to_dict())


async def _projects_update(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Update an existing project (partial patch).

    Returns:
        Resulting string.

    Raises:
        ArgumentValidationError: When ``project_id`` is not a UUID string.
    """
    tool = "synthorg_projects_update"
    try:
        args = typed_args(arguments, ProjectsUpdateArgs)
        try:
            UUID(args.project_id)
        except ValueError as uuid_exc:
            raise ArgumentValidationError(_ARG_PROJECT_ID, _TY_UUID) from uuid_exc
        project = await project_facade_service_of(app_state).update_project(
            project_id=args.project_id,
            actor_id=require_actor_id(actor),
            name=args.name,
            description=args.description,
            metadata=args.metadata,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if project is None:
        missing = NotFoundError(f"Project {args.project_id} not found")
        log_handler_invoke_failed(tool, missing, project_id=args.project_id)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(project.to_dict())


async def _projects_delete(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete a project (destructive; enforces guardrails).

    Returns:
        Resulting string.

    Raises:
        ArgumentValidationError: When ``project_id`` is not a UUID string.
    """
    tool = "synthorg_projects_delete"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        project_id = typed_args(arguments, ProjectsDeleteArgs).project_id
        try:
            UUID(project_id)
        except ValueError as uuid_exc:
            raise ArgumentValidationError(_ARG_PROJECT_ID, _TY_UUID) from uuid_exc
        actor_id = require_actor_id(resolved_actor)
        removed = await project_facade_service_of(app_state).delete_project(
            project_id=project_id,
            actor_id=actor_id,
            reason=reason,
        )
        if removed:
            logger.info(
                MCP_ADMIN_OP_EXECUTED,
                tool_name=tool,
                actor_agent_id=actor_id,
                reason=reason,
                project_id=project_id,
                removed=removed,
            )
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok({"removed": removed})


PROJECTS_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_projects_list": _projects_list,
        "synthorg_projects_get": _projects_get,
        "synthorg_projects_create": _projects_create,
        "synthorg_projects_update": _projects_update,
        "synthorg_projects_delete": _projects_delete,
    },
)
