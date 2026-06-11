"""Project MCP handlers (infrastructure sub-domain)."""

from collections.abc import Mapping
from types import MappingProxyType

from synthorg.api.state import AppState
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.infrastructure.state import project_facade_service_of
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    err,
    ok,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    coerce_pagination,
    get_optional_str,
    require_actor_id,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.meta.mcp.handlers.infrastructure._shared import (
    _get_dict,
    _require_str,
    _require_uuid,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_ADMIN_OP_EXECUTED

logger = get_logger(__name__)


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
        offset, limit = coerce_pagination(arguments)
        page, total = await project_facade_service_of(app_state).list_projects(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok([p.to_dict() for p in page], pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _projects_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single project by ID.

    Returns:
        Resulting string.
    """
    tool = "synthorg_projects_get"
    try:
        project_id = _require_uuid(arguments, "project_id")
        project = await project_facade_service_of(app_state).get_project(project_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if project is None:
        return err(
            LookupError(f"Project {project_id} not found"),
            domain_code="not_found",
        )
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
        name = _require_str(arguments, "name")
        description = _require_str(arguments, "description")
        metadata = _get_dict(arguments, "metadata")
        project = await project_facade_service_of(app_state).create_project(
            name=name,
            description=description,
            actor_id=require_actor_id(actor),
            metadata=metadata,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
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
    """
    tool = "synthorg_projects_update"
    try:
        project_id = _require_uuid(arguments, "project_id")
        name = get_optional_str(arguments, "name")
        description = get_optional_str(arguments, "description")
        metadata = _get_dict(arguments, "metadata")
        project = await project_facade_service_of(app_state).update_project(
            project_id=project_id,
            actor_id=require_actor_id(actor),
            name=name,
            description=description,
            metadata=metadata,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if project is None:
        return err(
            LookupError(f"Project {project_id} not found"),
            domain_code="not_found",
        )
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
    """
    tool = "synthorg_projects_delete"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        project_id = _require_uuid(arguments, "project_id")
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
