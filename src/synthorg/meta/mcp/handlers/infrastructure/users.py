"""API-user MCP handlers (infrastructure sub-domain)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.infrastructure.state import user_facade_service_of
from synthorg.meta.mcp.domains._remaining_args import (
    UsersCreateArgs,
    UsersDeleteArgs,
    UsersGetArgs,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import err, ok, require_admin_guardrails
from synthorg.meta.mcp.handlers.common_args import require_actor_id, require_dict
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.meta.mcp.handlers.infrastructure._shared import (
    _map_capability,
    _require_str,
    _to_jsonable,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_ADMIN_OP_EXECUTED

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


async def _users_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List registered API users.

    Returns:
        Resulting string.
    """
    tool = "synthorg_users_list"
    try:
        users = await user_facade_service_of(app_state).list_users()
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok([_to_jsonable(u) for u in users])


async def _users_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single API user by ID.

    Returns:
        Resulting string.
    """
    tool = "synthorg_users_get"
    try:
        user_id = typed_args(arguments, UsersGetArgs).user_id
        user = await user_facade_service_of(app_state).get_user(user_id)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if user is None:
        return err(
            LookupError(f"User {user_id} not found"),
            domain_code="not_found",
        )
    return ok(_to_jsonable(user))


async def _users_create(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Create a new API user (admin op; enforces guardrails).

    Returns:
        Resulting string.
    """
    tool = "synthorg_users_create"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        create_args = typed_args(arguments, UsersCreateArgs)
        username = create_args.username
        role = create_args.role
        actor_id = require_actor_id(resolved_actor)
        await user_facade_service_of(app_state).create_user(
            username=username,
            role=role,
            actor_id=actor_id,
        )
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=actor_id,
            reason=reason,
            username=username,
            role=role,
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
    return ok(None)


# lint-allow: handler-arguments-get -- cataloged mismatch: handler reads `updates`
# as a raw dict, but UsersUpdateArgs declares a typed `updates: UsersUpdateFields`
# (role / must_change_password only) that would reject other keys.
async def _users_update(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Update an existing API user (admin op; partial patch).

    Returns:
        Resulting string.
    """
    tool = "synthorg_users_update"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        user_id = _require_str(arguments, "user_id")
        updates = require_dict(arguments, "updates")
        actor_id = require_actor_id(resolved_actor)
        await user_facade_service_of(app_state).update_user(
            user_id=user_id,
            updates=updates,
            actor_id=actor_id,
        )
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=actor_id,
            reason=reason,
            user_id=user_id,
            update_keys=tuple(sorted(updates.keys())),
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
    return ok(None)


async def _users_delete(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete an API user (destructive; enforces guardrails).

    Returns:
        Resulting string.
    """
    tool = "synthorg_users_delete"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        user_id = typed_args(arguments, UsersDeleteArgs).user_id
        actor_id = require_actor_id(resolved_actor)
        await user_facade_service_of(app_state).delete_user(
            user_id=user_id,
            actor_id=actor_id,
            reason=reason,
        )
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=actor_id,
            reason=reason,
            user_id=user_id,
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
    return ok(None)


USERS_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_users_list": _users_list,
        "synthorg_users_get": _users_get,
        "synthorg_users_create": _users_create,
        "synthorg_users_update": _users_update,
        "synthorg_users_delete": _users_delete,
    },
)
