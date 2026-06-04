"""Department MCP handlers.

List / get / create / update / delete departments plus a per-department
health summary. Each handler shims through
:func:`department_service_of`. ``delete`` is destructive and enforces
the admin guardrail triple (confirm + reason + actor), emitting
``MCP_ADMIN_OP_EXECUTED`` on success.
"""

from typing import TYPE_CHECKING, Any

from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handlers._organization_helpers import (
    _require_str,
    _require_uuid,
)
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
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_ADMIN_OP_EXECUTED
from synthorg.organization.state import department_service_of

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)


async def _departments_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return a paginated slice of departments.

    Returns:
        Resulting string.
    """
    tool = "synthorg_departments_list"
    try:
        offset, limit = coerce_pagination(arguments)
        page, total = await department_service_of(app_state).list_departments(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok([d.to_dict() for d in page], pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _departments_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single department by UUID.

    Returns:
        Resulting string.
    """
    tool = "synthorg_departments_get"
    try:
        department_id = _require_uuid(arguments, "department_id")
        record = await department_service_of(app_state).get_department(department_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if record is None:
        return err(
            LookupError(f"Department {department_id} not found"),
            domain_code="not_found",
        )
    return ok(record.to_dict())


async def _departments_create(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Create a new department record (non-destructive write).

    Returns:
        Resulting string.
    """
    tool = "synthorg_departments_create"
    try:
        name = _require_str(arguments, "name")
        description = _require_str(arguments, "description")
        record = await department_service_of(app_state).create_department(
            name=name,
            description=description,
            actor_id=require_actor_id(actor),
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(record.to_dict())


async def _departments_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Update name / description on an existing department (partial patch).

    Returns:
        Resulting string.
    """
    tool = "synthorg_departments_update"
    try:
        department_id = _require_uuid(arguments, "department_id")
        name = get_optional_str(arguments, "name")
        description = get_optional_str(arguments, "description")
        record = await department_service_of(app_state).update_department(
            department_id=department_id,
            actor_id=require_actor_id(actor),
            name=name,
            description=description,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if record is None:
        return err(
            LookupError(f"Department {department_id} not found"),
            domain_code="not_found",
        )
    return ok(record.to_dict())


async def _departments_delete(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete a department (destructive; enforces confirm + reason + actor).

    Returns:
        Resulting string.
    """
    tool = "synthorg_departments_delete"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        department_id = _require_uuid(arguments, "department_id")
        actor_id = require_actor_id(resolved_actor)
        removed = await department_service_of(app_state).delete_department(
            department_id=department_id,
            actor_id=actor_id,
            reason=reason,
        )
        if removed:
            logger.info(
                MCP_ADMIN_OP_EXECUTED,
                tool_name=tool,
                actor_agent_id=actor_id,
                reason=reason,
                department_id=department_id,
                removed=removed,
            )
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok({"removed": removed})


async def _departments_get_health(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return a lightweight health summary for a single department.

    Returns:
        Resulting string.
    """
    tool = "synthorg_departments_get_health"
    try:
        department_id = _require_uuid(arguments, "department_id")
        result = await department_service_of(app_state).get_health(department_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(dict(result))
