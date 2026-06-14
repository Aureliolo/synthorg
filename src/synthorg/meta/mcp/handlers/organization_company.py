"""Company-record MCP handlers.

Read / update the singleton company record, list + reorder its
departments, and walk the company version history. Each handler shims
through :func:`company_read_service_of`; capability gaps surface as the
typed ``not_supported`` envelope via :func:`_map_capability`.
"""

from typing import TYPE_CHECKING

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import NotFoundError
from synthorg.meta.mcp.domains._workflows_org_args import (
    CompanyReorderDepartmentsArgs,
    CompanyUpdateArgs,
    CompanyVersionsGetArgs,
)
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handlers._mcp_handler_common import (
    _map_capability,
    _to_jsonable,
    typed_args,
)
from synthorg.meta.mcp.handlers.common import err, ok
from synthorg.meta.mcp.handlers.common_args import require_actor_id
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS
from synthorg.organization.state import company_read_service_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


async def _company_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the current company record.

    Returns:
        Resulting string.
    """
    tool = "synthorg_company_get"
    try:
        company = await company_read_service_of(app_state).get_company()
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(_to_jsonable(company))


async def _company_update(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Apply a payload patch to the company record (non-destructive write).

    Returns:
        Resulting string.
    """
    tool = "synthorg_company_update"
    try:
        payload = typed_args(arguments, CompanyUpdateArgs).payload
        result = await company_read_service_of(app_state).update_company(
            payload=payload,
            actor_id=require_actor_id(actor),
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(_to_jsonable(result))


async def _company_list_departments(
    *,
    app_state: AppState,
    arguments: dict[str, object],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List every department attached to the company.

    Returns:
        Resulting string.
    """
    tool = "synthorg_company_list_departments"
    try:
        departments = await company_read_service_of(app_state).list_departments()
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok([_to_jsonable(d) for d in departments])


async def _company_reorder_departments(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Replace the department display order with the supplied sequence.

    Returns:
        Resulting string.
    """
    tool = "synthorg_company_reorder_departments"
    try:
        ids = typed_args(arguments, CompanyReorderDepartmentsArgs).department_ids
        await company_read_service_of(app_state).reorder_departments(
            department_ids=ids,
            actor_id=require_actor_id(actor),
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(None)


async def _company_versions_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List every snapshot in the company version history.

    Returns:
        Resulting string.
    """
    tool = "synthorg_company_versions_list"
    try:
        versions = await company_read_service_of(app_state).list_versions()
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok([_to_jsonable(v) for v in versions])


async def _company_versions_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single company version snapshot by ID.

    Returns:
        Resulting string.
    """
    tool = "synthorg_company_versions_get"
    try:
        version_id = typed_args(arguments, CompanyVersionsGetArgs).version_id
        version = await company_read_service_of(app_state).get_version(version_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if version is None:
        missing = NotFoundError(f"Version {version_id} not found")
        log_handler_invoke_failed(tool, missing, version_id=version_id)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(_to_jsonable(version))
