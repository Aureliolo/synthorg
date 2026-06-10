"""Settings MCP handlers (infrastructure sub-domain)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.common import err, ok, require_admin_guardrails
from synthorg.meta.mcp.handlers.common_args import require_actor_id
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.meta.mcp.handlers.infrastructure._shared import (
    _map_capability,
    _require_str,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_ADMIN_OP_EXECUTED
from synthorg.settings.state import settings_read_service_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


async def _settings_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List runtime settings via the settings-read facade.

    Returns:
        Resulting string.
    """
    tool = "synthorg_settings_list"
    try:
        result = await settings_read_service_of(app_state).list_settings()
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(dict(result))


async def _settings_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single setting by key.

    Returns:
        Resulting string.
    """
    tool = "synthorg_settings_get"
    try:
        key = _require_str(arguments, "key")
        result = await settings_read_service_of(app_state).get_setting(key)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok({"key": key, "value": result})


async def _settings_update(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Update or create a setting value (admin op; enforces guardrails).

    Returns:
        Resulting string.
    """
    tool = "synthorg_settings_update"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        key = _require_str(arguments, "key")
        value = arguments.get("value")
        actor_id = require_actor_id(resolved_actor)
        await settings_read_service_of(app_state).update_setting(
            key=key,
            value=value,
            actor_id=actor_id,
        )
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=actor_id,
            reason=reason,
            key=key,
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


async def _settings_delete(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete a setting key (destructive; enforces guardrails).

    Returns:
        Resulting string.
    """
    tool = "synthorg_settings_delete"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        key = _require_str(arguments, "key")
        actor_id = require_actor_id(resolved_actor)
        await settings_read_service_of(app_state).delete_setting(
            key=key,
            actor_id=actor_id,
            reason=reason,
        )
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=actor_id,
            reason=reason,
            key=key,
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


SETTINGS_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_settings_list": _settings_list,
        "synthorg_settings_get": _settings_get,
        "synthorg_settings_update": _settings_update,
        "synthorg_settings_delete": _settings_delete,
    },
)
