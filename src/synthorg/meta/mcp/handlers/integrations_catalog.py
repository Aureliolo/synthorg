"""MCP-catalog integration handlers.

List / search / get / install / uninstall for the MCP server catalog.
Each handler shims through :func:`mcp_catalog_facade_service_of`;
``uninstall`` is destructive and enforces the admin guardrail triple,
emitting ``MCP_ADMIN_OP_EXECUTED`` on success.
"""

from typing import TYPE_CHECKING

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.infrastructure.state import mcp_catalog_facade_service_of
from synthorg.meta.mcp.domains._remaining_args import (
    McpCatalogGetArgs,
    McpCatalogInstallArgs,
    McpCatalogListArgs,
    McpCatalogSearchArgs,
    McpCatalogUninstallArgs,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handlers._mcp_handler_common import (
    _map_capability,
    _to_jsonable,
    typed_args,
)
from synthorg.meta.mcp.handlers.common import (
    err,
    ok,
    paginate_sequence,
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
from synthorg.observability.events.mcp import MCP_ADMIN_OP_EXECUTED

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


async def _mcp_catalog_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List available MCP catalog entries (paginated).

    Returns:
        Resulting string.
    """
    tool = "synthorg_mcp_catalog_list"
    try:
        page_args = typed_args(arguments, McpCatalogListArgs)
        offset, limit = page_args.offset, page_args.limit
        entries = await mcp_catalog_facade_service_of(app_state).list_catalog()
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    sequence = tuple(entries)
    page, pagination = paginate_sequence(
        sequence,
        offset=offset,
        limit=limit,
        total=len(sequence),
    )
    return ok([_to_jsonable(e) for e in page], pagination=pagination)


async def _mcp_catalog_search(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Search MCP catalog entries by query string.

    Returns:
        Resulting string.
    """
    tool = "synthorg_mcp_catalog_search"
    try:
        query = typed_args(arguments, McpCatalogSearchArgs).query
        entries = await mcp_catalog_facade_service_of(app_state).search_catalog(query)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok([_to_jsonable(e) for e in entries])


async def _mcp_catalog_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single MCP catalog entry by ID.

    Returns:
        Resulting string.
    """
    tool = "synthorg_mcp_catalog_get"
    try:
        entry_id = typed_args(arguments, McpCatalogGetArgs).entry_id
        entry = await mcp_catalog_facade_service_of(app_state).get_catalog_entry(
            entry_id,
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
    if entry is None:
        return err(
            LookupError(f"MCP catalog entry {entry_id} not found"),
            domain_code="not_found",
        )
    return ok(_to_jsonable(entry))


async def _mcp_catalog_install(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
    # lint-allow: mcp-admin-guardrail -- install records new entry; no state mutated
) -> str:
    """Install an MCP catalog entry (non-destructive create).

    Returns:
        Resulting string.
    """
    tool = "synthorg_mcp_catalog_install"
    try:
        entry_id = typed_args(arguments, McpCatalogInstallArgs).entry_id
        result = await mcp_catalog_facade_service_of(app_state).install_catalog_entry(
            entry_id=entry_id,
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
    return ok(_to_jsonable(result))


async def _mcp_catalog_uninstall(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Uninstall an MCP catalog entry (destructive; enforces guardrails).

    Returns:
        Resulting string.
    """
    tool = "synthorg_mcp_catalog_uninstall"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        installation_id = typed_args(arguments, McpCatalogUninstallArgs).installation_id
        actor_id = require_actor_id(resolved_actor)
        mcp_catalog = mcp_catalog_facade_service_of(app_state)
        removed = await mcp_catalog.uninstall_catalog_entry(
            installation_id=installation_id,
            actor_id=actor_id,
            reason=reason,
        )
        if removed:
            logger.info(
                MCP_ADMIN_OP_EXECUTED,
                tool_name=tool,
                actor_agent_id=actor_id,
                reason=reason,
                installation_id=installation_id,
                removed=removed,
            )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
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
