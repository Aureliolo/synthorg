"""Fine-tune checkpoint MCP handlers.

List / deploy / rollback / delete for fine-tune checkpoints. Deploy /
rollback / delete enforce the admin guardrail triple and emit
``MCP_ADMIN_OP_EXECUTED`` on success; ``MemoryBackendUnsupportedError``
forwards to the ``not_supported`` envelope.
"""

from typing import TYPE_CHECKING

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import QueryError
from synthorg.memory.fine_tune_plan import MemoryBackendUnsupportedError
from synthorg.memory.service import (
    CheckpointNotFoundError,
    CheckpointRollbackCorruptError,
    CheckpointRollbackUnavailableError,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handlers._memory_service_helpers import (
    _ARG_CHECKPOINT_ID,
    _service,
)
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    dump_many,
    err,
    not_supported,
    ok,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    actor_id,
    coerce_pagination,
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
    from synthorg.api.state import AppState

logger = get_logger(__name__)


async def _memory_list_checkpoints(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_memory_list_checkpoints`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_memory_list_checkpoints"
    try:
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        service = _service(app_state)
    except MemoryBackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    try:
        checkpoints, total = await service.list_checkpoints(
            limit=limit,
            offset=offset,
        )
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(checkpoints), pagination=meta)


async def _memory_deploy_checkpoint(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_memory_deploy_checkpoint`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_memory_deploy_checkpoint"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        checkpoint_id = require_non_blank(arguments, _ARG_CHECKPOINT_ID)
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        service = _service(app_state)
    except MemoryBackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    try:
        cp = await service.deploy_checkpoint(checkpoint_id)
    except CheckpointNotFoundError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except QueryError as exc:
        # Persistence-layer failure during deploy (e.g. the checkpoint
        # was activated but the re-read failed) -- surface as
        # ``conflict`` so callers distinguish from internal errors.
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="conflict")
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_ADMIN_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=actor_id(resolved_actor),
        reason=reason,
        target_id=checkpoint_id,
    )
    return ok(data=cp.model_dump(mode="json"))


async def _memory_rollback_checkpoint(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_memory_rollback_checkpoint`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_memory_rollback_checkpoint"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        checkpoint_id = require_non_blank(arguments, _ARG_CHECKPOINT_ID)
        service = _service(app_state)
        cp = await service.rollback_checkpoint(checkpoint_id)
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except MemoryBackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    except CheckpointNotFoundError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except (
        CheckpointRollbackUnavailableError,
        CheckpointRollbackCorruptError,
    ) as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="conflict")
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_ADMIN_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=actor_id(resolved_actor),
        reason=reason,
        target_id=checkpoint_id,
    )
    return ok(data=cp.model_dump(mode="json"))


async def _memory_delete_checkpoint(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_memory_delete_checkpoint`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_memory_delete_checkpoint"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        checkpoint_id = require_non_blank(arguments, _ARG_CHECKPOINT_ID)
        service = _service(app_state)
        await service.delete_checkpoint(checkpoint_id)
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except MemoryBackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    except CheckpointNotFoundError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except QueryError as exc:
        # Active-checkpoint / domain-rule violation -- surface as
        # ``conflict`` so callers can distinguish from internal errors.
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="conflict")
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)

    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_ADMIN_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=actor_id(resolved_actor),
        reason=reason,
        target_id=checkpoint_id,
    )
    return ok()
