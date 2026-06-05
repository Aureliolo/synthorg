"""Memory-entry deletion MCP handler.

Deletes a single agent-owned memory entry. Enforces the admin guardrail
triple and emits ``MCP_ADMIN_OP_EXECUTED`` on success. Routes through
:func:`_delete_entry_service`, which only needs a ``MemoryBackend`` (no
fine-tune repositories), so memory-only deployments retain deletion.
"""

from typing import TYPE_CHECKING, Any

from synthorg.core.critical_errors import reraise_critical
from synthorg.memory.fine_tune_plan import MemoryBackendUnsupportedError
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handlers._memory_service_helpers import (
    _ARG_AGENT_ID,
    _ARG_MEMORY_ID,
    _delete_entry_service,
)
from synthorg.meta.mcp.handlers.common import (
    err,
    not_supported,
    ok,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    actor_id,
    require_non_blank,
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

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)


async def _memory_delete_entry(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete a single memory entry owned by an agent.

    Required arguments: ``agent_id``, ``memory_id``, plus the
    destructive-op guardrail triple (``confirm=True``, non-blank
    ``reason``, identifiable actor).

    Returns:
        Resulting string.
    """
    tool = "synthorg_memory_delete_entry"
    agent_id = ""
    memory_id = ""
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        agent_id = require_non_blank(arguments, _ARG_AGENT_ID)
        memory_id = require_non_blank(arguments, _ARG_MEMORY_ID)
        deleted = await _delete_entry_service(app_state).delete_memory_entry(
            agent_id,
            memory_id,
        )
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except MemoryBackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc, agent_id=agent_id, memory_id=memory_id)
        return err(exc)
    if not deleted:
        not_found_exc = ValueError(f"memory entry {memory_id!r} not found")
        log_handler_invoke_failed(
            tool,
            not_found_exc,
            agent_id=agent_id,
            memory_id=memory_id,
        )
        return err(not_found_exc, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_ADMIN_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=actor_id(resolved_actor),
        reason=reason,
        target_id=memory_id,
        agent_id=agent_id,
    )
    return ok()
