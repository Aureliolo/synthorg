"""Department MCP handlers.

List / get / create / update / delete departments plus a per-department
health summary. Each handler shims through
:func:`department_service_of`. ``delete`` is destructive and enforces
the admin guardrail triple (confirm + reason + actor), emitting
``MCP_ADMIN_OP_EXECUTED`` on success.
"""

from typing import TYPE_CHECKING

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import NotFoundError
from synthorg.meta.mcp.domains._workflows_org_args import (
    DepartmentsCreateArgs,
    DepartmentsDeleteArgs,
    DepartmentsGetArgs,
    DepartmentsGetHealthArgs,
    DepartmentsListArgs,
    DepartmentsUpdateArgs,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handlers._mcp_handler_common import (
    typed_args,
)
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    capability_gap,
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
from synthorg.organization.state import (
    OrganizationStateSlice,
    department_service_of,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_WHY_DEPT_NOT_WIRED = "department_service is not wired on app_state in this deployment"


def _department_service_wired(app_state: AppState) -> bool:
    """Return whether the department service is attached to ``app_state``.

    Returns:
        ``True`` when the service slot is populated, ``False`` otherwise.
    """
    return app_state.slice(OrganizationStateSlice).department_service is not None


async def _departments_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return a paginated slice of departments.

    Returns:
        Resulting string.
    """
    tool = "synthorg_departments_list"
    if not _department_service_wired(app_state):
        return capability_gap(tool, _WHY_DEPT_NOT_WIRED)
    try:
        page_args = typed_args(arguments, DepartmentsListArgs)
        offset, limit = page_args.offset, page_args.limit
        page, total = await department_service_of(app_state).list_departments(
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
    return ok([d.to_dict() for d in page], pagination=pagination)


async def _departments_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single department by UUID.

    Returns:
        Resulting string.
    """
    tool = "synthorg_departments_get"
    if not _department_service_wired(app_state):
        return capability_gap(tool, _WHY_DEPT_NOT_WIRED)
    try:
        department_id = typed_args(arguments, DepartmentsGetArgs).department_id
        record = await department_service_of(app_state).get_department(department_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if record is None:
        missing = NotFoundError(f"Department {department_id} not found")
        log_handler_invoke_failed(tool, missing, department_id=department_id)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(record.to_dict())


async def _departments_create(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Create a new department record (non-destructive write).

    Returns:
        Resulting string.
    """
    tool = "synthorg_departments_create"
    if not _department_service_wired(app_state):
        return capability_gap(tool, _WHY_DEPT_NOT_WIRED)
    try:
        args = typed_args(arguments, DepartmentsCreateArgs)
        record = await department_service_of(app_state).create_department(
            name=args.name,
            description=args.description,
            actor_id=require_actor_id(actor),
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(record.to_dict())


async def _departments_update(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Update name / description on an existing department (partial patch).

    Returns:
        Resulting string.
    """
    tool = "synthorg_departments_update"
    if not _department_service_wired(app_state):
        return capability_gap(tool, _WHY_DEPT_NOT_WIRED)
    try:
        args = typed_args(arguments, DepartmentsUpdateArgs)
        department_id = args.department_id
        record = await department_service_of(app_state).update_department(
            department_id=department_id,
            actor_id=require_actor_id(actor),
            name=args.name,
            description=args.description,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if record is None:
        missing = NotFoundError(f"Department {department_id} not found")
        log_handler_invoke_failed(tool, missing, department_id=department_id)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(record.to_dict())


async def _departments_delete(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete a department (destructive; enforces confirm + reason + actor).

    Returns:
        Resulting string.
    """
    tool = "synthorg_departments_delete"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        department_id = typed_args(arguments, DepartmentsDeleteArgs).department_id
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    if not _department_service_wired(app_state):
        return capability_gap(tool, _WHY_DEPT_NOT_WIRED)
    actor_id = require_actor_id(resolved_actor)
    try:
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
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok({"removed": removed})


async def _departments_get_health(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return a lightweight health summary for a single department.

    Returns:
        Resulting string.
    """
    tool = "synthorg_departments_get_health"
    if not _department_service_wired(app_state):
        return capability_gap(tool, _WHY_DEPT_NOT_WIRED)
    try:
        department_id = typed_args(arguments, DepartmentsGetHealthArgs).department_id
        result = await department_service_of(app_state).get_health(department_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(dict(result))
