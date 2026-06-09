"""Setup-wizard MCP handlers (infrastructure sub-domain)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.agent import AgentIdentity
from synthorg.infrastructure.state import setup_facade_service_of
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.common import err, ok, require_admin_guardrails
from synthorg.meta.mcp.handlers.common_args import require_actor_id, require_dict
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.meta.mcp.handlers.infrastructure._shared import _map_capability
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_ADMIN_OP_EXECUTED

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


async def _setup_get_status(
    *,
    app_state: AppState,
    arguments: dict[str, object],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return current setup-wizard state.

    Returns:
        Resulting string.
    """
    tool = "synthorg_setup_get_status"
    try:
        status = await setup_facade_service_of(app_state).get_status()
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(dict(status))


async def _setup_initialize(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Dispatch an initialisation step (admin op; delegates to setup controller).

    Returns:
        Resulting string.
    """
    tool = "synthorg_setup_initialize"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        config = require_dict(arguments, "config")
        await setup_facade_service_of(app_state).initialize(config=config)
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=require_actor_id(resolved_actor),
            reason=reason,
            config_keys=tuple(sorted(config.keys())),
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(None)


SETUP_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_setup_get_status": _setup_get_status,
        "synthorg_setup_initialize": _setup_initialize,
    },
)
