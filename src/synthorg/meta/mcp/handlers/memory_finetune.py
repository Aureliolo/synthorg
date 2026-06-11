"""Fine-tune lifecycle + run/embedder MCP handlers.

Start / resume / status / cancel / preflight for the memory fine-tune
pipeline, plus run listing and the active-embedder read. The privileged
ops (start / resume / cancel) enforce the admin guardrail triple and
emit ``MCP_ADMIN_OP_EXECUTED`` on success. ``MemoryBackendUnsupportedError``
is forwarded to the ``not_supported`` envelope.
"""

from typing import TYPE_CHECKING

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import FineTuneRunActiveError
from synthorg.core.types import NotBlankStr
from synthorg.memory.fine_tune_plan import MemoryBackendUnsupportedError
from synthorg.memory.service import (
    FineTuneRunNotFoundError,
    FineTuneRunNotResumableError,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handlers._memory_finetune_parse import parse_fine_tune_plan
from synthorg.meta.mcp.handlers._memory_service_helpers import (
    _ARG_RUN_ID,
    _TY_NON_BLANK,
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


async def _memory_start_fine_tune(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_memory_start_fine_tune`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.

    Raises:
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
    """
    tool = "synthorg_memory_start_fine_tune"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        plan = parse_fine_tune_plan(arguments)
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        service = _service(app_state)
        run = await service.start_fine_tune(plan)
    except MemoryBackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    except FineTuneRunActiveError as exc:
        # The orchestrator raises this when another run is already
        # active; surface it as a conflict so callers get a typed
        # recovery path instead of a generic handler error.
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
        target_id=str(run.id),
    )
    return ok(data=run.model_dump(mode="json"))


async def _memory_resume_fine_tune(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_memory_resume_fine_tune`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_memory_resume_fine_tune"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        run_id = require_non_blank(arguments, _ARG_RUN_ID)
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        service = _service(app_state)
        run = await service.resume_fine_tune(NotBlankStr(run_id))
    except MemoryBackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    except FineTuneRunActiveError as exc:
        # Another run is already active -- same ``conflict`` mapping
        # as :func:`_memory_start_fine_tune`.
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="conflict")
    except (FineTuneRunNotFoundError, FineTuneRunNotResumableError) as exc:
        # Typed exceptions carry their own ``domain_code`` class
        # attribute (``not_found`` / ``conflict``), so ``err(exc)``
        # picks up the right wire contract without regex-matching
        # the message -- any future wording change in the
        # orchestrator won't reclassify the failure.
        log_handler_invoke_failed(tool, exc)
        return err(exc)
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
        target_id=str(run.id),
    )
    return ok(data=run.model_dump(mode="json"))


async def _memory_get_fine_tune_status(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_memory_get_fine_tune_status`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_memory_get_fine_tune_status"
    run_id_raw = arguments.get(_ARG_RUN_ID)
    run_id: NotBlankStr | None = None
    if run_id_raw is not None:
        if not isinstance(run_id_raw, str) or not run_id_raw.strip():
            exc = ArgumentValidationError(_ARG_RUN_ID, _TY_NON_BLANK)
            log_handler_argument_invalid(tool, exc)
            return err(exc)
        run_id = NotBlankStr(run_id_raw.strip())
    try:
        service = _service(app_state)
        status = await service.get_fine_tune_status(run_id)
    except MemoryBackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    except ValueError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=status.model_dump(mode="json"))


async def _memory_cancel_fine_tune(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_memory_cancel_fine_tune`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_memory_cancel_fine_tune"
    try:
        reason, resolved_actor = require_admin_guardrails(
            arguments,
            actor,
        )
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    try:
        service = _service(app_state)
        target_id = await service.cancel_fine_tune()
    except MemoryBackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    # Only emit the admin-op audit when something was actually
    # cancelled. A ``None`` target means the orchestrator had no active
    # run, and emitting ``MCP_ADMIN_OP_EXECUTED`` with
    # ``target_id=None`` would plant a false no-op entry in the audit
    # trail. Operators investigating a cancellation should never see a
    # record for a cancel that did not happen.
    if target_id is not None:
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=actor_id(resolved_actor),
            reason=reason,
            target_id=target_id,
        )
    return ok()


async def _memory_run_preflight(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001 lint-allow: mcp-admin-guardrail -- preflight is read-only validation, not a mutation
) -> str:
    """Handle the ``synthorg_memory_run_preflight`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_memory_run_preflight"
    try:
        plan = parse_fine_tune_plan(arguments)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        service = _service(app_state)
        result = await service.run_preflight(plan)
    except MemoryBackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=result.model_dump(mode="json"))


async def _memory_list_runs(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_memory_list_runs`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_memory_list_runs"
    try:
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        service = _service(app_state)
        runs, total = await service.list_runs(limit=limit, offset=offset)
    except MemoryBackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(runs), pagination=meta)


async def _memory_get_active_embedder(
    *,
    app_state: AppState,
    arguments: dict[str, object],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_memory_get_active_embedder`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_memory_get_active_embedder"
    try:
        service = _service(app_state)
        snap = await service.get_active_embedder()
    except MemoryBackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=snap.model_dump(mode="json"))
