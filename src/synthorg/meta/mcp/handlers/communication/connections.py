"""External-connection MCP handlers (communication sub-domain)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.state import connection_service_of
from synthorg.meta.mcp.domains._remaining_args import (
    ConnectionsCheckHealthArgs,
    ConnectionsCreateArgs,
    ConnectionsDeleteArgs,
    ConnectionsGetArgs,
    ConnectionsListArgs,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    dump_many,
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
from synthorg.observability.events.mcp import MCP_ADMIN_OP_EXECUTED

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_ARG_CONNECTION_TYPE = "connection_type"
_TY_CONNECTION_TYPE = "ConnectionType string"


async def _connections_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List external-system connections (paginated).

    Returns:
        Resulting string.
    """
    try:
        page_args = typed_args(arguments, ConnectionsListArgs)
        offset, limit = page_args.offset, page_args.limit
        connections, total = await connection_service_of(app_state).list_connections(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok(dump_many(connections), pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_connections_list", exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed("synthorg_connections_list", exc)
        return err(exc)


async def _connections_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single connection by name.

    Returns:
        Resulting string.
    """
    try:
        name = typed_args(arguments, ConnectionsGetArgs).name
        connection = await connection_service_of(app_state).get_connection(name)
        if connection is None:
            return err(
                LookupError(f"Connection {name} not found"),
                domain_code="not_found",
            )
        return ok(connection.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_connections_get", exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed("synthorg_connections_get", exc)
        return err(exc)


async def _connections_create(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Create a new external connection (admin op; enforces guardrails).

    Returns:
        Resulting string.

    Raises:
        ArgumentValidationError: When ``connection_type`` is not a known
            :class:`ConnectionType` value.
    """
    tool = "synthorg_connections_create"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        args = typed_args(arguments, ConnectionsCreateArgs)
        try:
            connection_type = ConnectionType(args.connection_type)
        except ValueError as exc:
            bad = ArgumentValidationError(_ARG_CONNECTION_TYPE, _TY_CONNECTION_TYPE)
            raise bad from exc
        actor_id = require_actor_id(resolved_actor)
        connection = await connection_service_of(app_state).create_connection(
            name=args.name,
            connection_type=connection_type,
            auth_method=args.auth_method,
            credentials=args.credentials,
            actor_id=actor_id,
            base_url=args.base_url,
            metadata=args.metadata,
        )
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=actor_id,
            reason=reason,
            connection_name=args.name,
        )
        return ok(connection.model_dump(mode="json"))
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


async def _connections_delete(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete a connection (destructive; enforces guardrails).

    Returns:
        Resulting string.
    """
    tool = "synthorg_connections_delete"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        name = typed_args(arguments, ConnectionsDeleteArgs).name
        actor_id = require_actor_id(resolved_actor)
        await connection_service_of(app_state).delete_connection(
            name=name,
            actor_id=actor_id,
            reason=reason,
        )
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=actor_id,
            reason=reason,
            connection_name=name,
        )
        return ok(None)
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


async def _connections_check_health(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Run an on-demand health probe against a connection.

    Returns:
        Resulting string.
    """
    try:
        name = typed_args(arguments, ConnectionsCheckHealthArgs).name
        connection = await connection_service_of(app_state).check_health(name=name)
        if connection is None:
            return err(
                LookupError(f"Connection {name} not found"),
                domain_code="not_found",
            )
        return ok(connection.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_connections_check_health", exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed("synthorg_connections_check_health", exc)
        return err(exc)


CONNECTIONS_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_connections_list": _connections_list,
        "synthorg_connections_get": _connections_get,
        "synthorg_connections_create": _connections_create,
        "synthorg_connections_delete": _connections_delete,
        "synthorg_connections_check_health": _connections_check_health,
    },
)
