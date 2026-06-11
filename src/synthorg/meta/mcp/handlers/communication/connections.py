"""External-connection MCP handlers (communication sub-domain)."""

from collections.abc import Mapping
from types import MappingProxyType

from synthorg.api.state import AppState
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.state import connection_service_of
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    dump_many,
    err,
    ok,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    coerce_pagination,
    get_optional_str,
    require_actor_id,
    require_arg,
    require_dict,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.meta.mcp.handlers.communication._shared import (
    _get_dict,
    _require_str,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_ADMIN_OP_EXECUTED

logger = get_logger(__name__)

_ARG_NAME = "name"
_ARG_CONNECTION_TYPE = "connection_type"
_ARG_AUTH_METHOD = "auth_method"
_ARG_CREDENTIALS = "credentials"
_ARG_BASE_URL = "base_url"
_ARG_METADATA = "metadata"
_TY_CONNECTION_TYPE = "ConnectionType string"


def _parse_connection_type(arguments: dict[str, object]) -> ConnectionType:
    """Return parse connection type."""
    raw = require_arg(arguments, _ARG_CONNECTION_TYPE, str)
    try:
        return ConnectionType(raw)
    except ValueError as exc:
        err = ArgumentValidationError(_ARG_CONNECTION_TYPE, _TY_CONNECTION_TYPE)
        raise err from exc


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
        offset, limit = coerce_pagination(arguments)
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
        name = _require_str(arguments, _ARG_NAME)
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
    """
    tool = "synthorg_connections_create"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        name = _require_str(arguments, _ARG_NAME)
        connection_type = _parse_connection_type(arguments)
        auth_method = _require_str(arguments, _ARG_AUTH_METHOD)
        credentials = require_dict(arguments, _ARG_CREDENTIALS, value_type=str)
        base_url = get_optional_str(arguments, _ARG_BASE_URL)
        metadata = _get_dict(arguments, _ARG_METADATA)
        actor_id = require_actor_id(resolved_actor)
        connection = await connection_service_of(app_state).create_connection(
            name=name,
            connection_type=connection_type,
            auth_method=auth_method,
            credentials=credentials,
            actor_id=actor_id,
            base_url=base_url,
            metadata=metadata,
        )
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=actor_id,
            reason=reason,
            connection_name=name,
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
        name = _require_str(arguments, _ARG_NAME)
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
        name = _require_str(arguments, _ARG_NAME)
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
