"""Sandbox-tunnel MCP handlers (communication sub-domain)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.state import tunnel_service_of
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
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_ADMIN_OP_EXECUTED

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


async def _tunnel_get_status(
    *,
    app_state: AppState,
    arguments: dict[str, object],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the current tunnel service status.

    Returns:
        Resulting string.
    """
    try:
        status = await tunnel_service_of(app_state).get_status()
        return ok(status.to_dict())
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed("synthorg_tunnel_get_status", exc)
        return err(exc)


async def _tunnel_connect(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Trigger a tunnel reconnect attempt (admin op; enforces guardrails).

    Returns:
        Resulting string.
    """
    tool = "synthorg_tunnel_connect"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        actor_id = require_actor_id(resolved_actor)
        status = await tunnel_service_of(app_state).connect()
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=actor_id,
            reason=reason,
        )
        return ok(status.to_dict())
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


TUNNEL_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_tunnel_get_status": _tunnel_get_status,
        "synthorg_tunnel_connect": _tunnel_connect,
    },
)
