"""Health-check MCP handler (infrastructure sub-domain)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.approval.state import ApprovalStateSlice
from synthorg.budget.state import BudgetStateSlice
from synthorg.core.agent import AgentIdentity
from synthorg.engine.state import EngineStateSlice
from synthorg.hr.state import HrStateSlice
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.common import err, ok
from synthorg.meta.mcp.handlers.common_logging import log_handler_invoke_failed
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


async def _health_check(
    *,
    app_state: AppState,
    arguments: dict[str, object],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return lightweight health status for the AppState subsystems.

    Returns:
        Resulting string.
    """
    tool = "synthorg_health_check"
    try:
        data = {
            "task_engine": app_state.slice(EngineStateSlice).task_engine is not None,
            "cost_tracker": app_state.slice(BudgetStateSlice).cost_tracker is not None,
            "approval_store": app_state.slice(ApprovalStateSlice).store is not None,
            "agent_registry": app_state.slice(HrStateSlice).agent_registry is not None,
        }
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        # No argument validation in this body, so the canonical
        # ``except ArgumentValidationError`` branch added across other
        # handlers would be dead code here. Capability flag access is
        # the only thing that can fail.
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=data)


HEALTH_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_health_check": _health_check,
    },
)
