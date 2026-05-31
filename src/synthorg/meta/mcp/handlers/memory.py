"""Memory domain MCP handlers (fine-tune checkpoints + runs + entries).

Wires 12 tools through :class:`MemoryService`. The service is
injected via ``app_state.memory_service`` by the application
bootstrap; handlers route through that facade exclusively and never
reach into ``app_state.persistence.*`` directly (CLAUDE.md
persistence-boundary rule).

Backend-unsupported routing. :class:`MemoryBackendUnsupportedError` is
raised in two well-defined places: (1) :class:`MemoryService`
fine-tune lifecycle methods when the active persistence backend does
not expose fine-tune repos, and (2) :func:`_service` here when no
:class:`MemoryService` is wired at all (stripped-down test app-states,
unsupported backends). Every handler in this module catches the
exception and forwards it -- without exception -- to
:func:`not_supported`, which both:

- returns the shared ``not_supported`` wire envelope
  (``{"status": "error", "domain_code": "not_supported"}``), and
- emits the :data:`MCP_HANDLER_NOT_IMPLEMENTED` WARNING event so ops
  telemetry can distinguish backend-unsupported from fully-wired but
  method-missing primitives (``capability_gap`` path).

Privileged ops. ``start_fine_tune`` / ``resume_fine_tune`` (which launch
the pipeline, including the internal model-swapping deploy stage),
``deploy_checkpoint`` (a standalone model swap), and the destructive
``cancel_fine_tune`` / ``rollback_checkpoint`` / ``delete_checkpoint``
all enforce the guardrail triple (actor + ``confirm`` + ``reason``) at
the handler boundary and emit :data:`MCP_ADMIN_OP_EXECUTED` on success.
"""

import copy
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import FineTuneRunActiveError
from synthorg.core.persistence_errors import PersistenceConnectionError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.memory.fine_tune_plan import (
    MemoryBackendUnsupportedError,
)
from synthorg.memory.service import (
    CheckpointNotFoundError,
    CheckpointRollbackCorruptError,
    CheckpointRollbackUnavailableError,
    FineTuneRunNotFoundError,
    FineTuneRunNotResumableError,
    MemoryService,
)
from synthorg.memory.state import MemoryStateSlice, memory_service_of
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,
)
from synthorg.meta.mcp.handlers._memory_finetune_parse import parse_fine_tune_plan
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
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.settings.state import SettingsStateSlice

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)


_TY_NON_BLANK = "non-blank string"
_ARG_CHECKPOINT_ID = "checkpoint_id"
_ARG_RUN_ID = "run_id"
_ARG_AGENT_ID = "agent_id"
_ARG_MEMORY_ID = "memory_id"


_WHY_MEMORY_SERVICE_NOT_WIRED = (
    "memory service is not wired on the active application state; "
    "fine-tune endpoints require an injected MemoryService and are "
    "unavailable on backends that do not support fine-tune repositories"
)


_WHY_BACKEND_NO_FINE_TUNE = (
    "fine-tune repositories are not exposed by the active persistence "
    "backend; ensure the backend is connected and exposes "
    "fine_tune_runs + fine_tune_checkpoints (both SQLite and Postgres "
    "do today)"
)


def _service(app_state: Any) -> MemoryService:
    """Return the injected :class:`MemoryService` facade.

    Handlers route through ``app_state.memory_service`` exclusively
    (CLAUDE.md persistence-boundary rule). For app_states that have
    adopted the wired-service pattern, :attr:`has_memory_service`
    short-circuits the lookup. As a fallback for stripped-down test
    app-states that expose only a raw ``persistence`` backend, we try
    to construct a service on the fly from
    ``persistence.fine_tune_checkpoints`` / ``.fine_tune_runs``.

    Every failure mode raises :class:`MemoryBackendUnsupportedError` so the
    calling handler returns a uniform ``not_supported`` envelope:

    * No wired service **and** the raw backend is absent / doesn't expose
      fine-tune repos.
    * The backend's fine-tune property raises ``NotImplementedError``
      (legacy / partial backend).
    * The backend is not yet connected and the property's
      ``_require_connected`` guard raises
      :class:`~synthorg.core.persistence_errors.PersistenceConnectionError`.

    Raises:
        MemoryBackendUnsupportedError: In any of the above cases.

    Returns:
        ``MemoryService`` instance.
    """
    if app_state.slice(MemoryStateSlice).service is not None:
        attached: MemoryService = memory_service_of(app_state)
        return attached
    # Probe the raw instance dict so we do not trigger
    # ``AppState.memory_service`` (a property descriptor that raises
    # ``RuntimeError`` when the slot has not been set). The facade-first
    # short-circuit above already covered the wired path; this branch
    # only exists for stripped-down test app-states that set
    # ``memory_service`` as a plain attribute on a ``SimpleNamespace``.
    raw_cached = (
        vars(app_state).get("memory_service")
        if hasattr(
            app_state,
            "__dict__",
        )
        else None
    )
    if isinstance(raw_cached, MemoryService):
        return raw_cached
    backend = app_state.slice(PersistenceStateSlice).backend
    if backend is None:
        raise MemoryBackendUnsupportedError(_WHY_MEMORY_SERVICE_NOT_WIRED)
    try:
        checkpoint_repo = backend.fine_tune_checkpoints
        run_repo = backend.fine_tune_runs
    except (
        NotImplementedError,
        PersistenceConnectionError,
        AttributeError,
    ) as exc:
        # ``AttributeError`` covers partial backends that simply lack
        # the property altogether; without catching it here the handler
        # would surface a generic 500 instead of the contract-stipulated
        # ``not_supported`` envelope.
        raise MemoryBackendUnsupportedError(_WHY_BACKEND_NO_FINE_TUNE) from exc
    settings_service = app_state.slice(SettingsStateSlice).settings_service
    return MemoryService(
        checkpoint_repo=checkpoint_repo,
        run_repo=run_repo,
        settings_service=settings_service,
        memory_backend=app_state.slice(MemoryStateSlice).backend,
    )


def _delete_entry_service(app_state: Any) -> MemoryService:
    """Return a :class:`MemoryService` suitable for memory-entry deletion.

    Sibling of :func:`_service` that does **not** require fine-tune
    repositories. The ``delete_memory_entry`` path only needs a
    :class:`MemoryBackend`; treating missing fine-tune repos as fatal
    here would route every memory-only deployment through
    ``not_supported`` and silently disable user data deletion.

    Resolution order:

    1. The wired :class:`MemoryService` facade
       (``app_state.has_memory_service``).
    2. A cached ``MemoryService`` attached as a plain attribute
       (stripped-down test app-states).
    3. A freshly-built ``MemoryService`` constructed from a wired
       ``MemoryBackend`` (with optional ``settings_service``); fine-tune
       repos are intentionally left as ``None``.

    Raises:
        MemoryBackendUnsupportedError: When no service or backend is wired at
            all -- the only case where deletion truly cannot proceed.

    Returns:
        ``MemoryService`` instance.
    """
    if app_state.slice(MemoryStateSlice).service is not None:
        attached: MemoryService = memory_service_of(app_state)
        return attached
    raw_cached = (
        vars(app_state).get("memory_service")
        if hasattr(app_state, "__dict__")
        else None
    )
    if isinstance(raw_cached, MemoryService):
        return raw_cached
    backend = app_state.slice(MemoryStateSlice).backend
    if backend is None:
        raise MemoryBackendUnsupportedError(_WHY_MEMORY_SERVICE_NOT_WIRED)
    settings_service = app_state.slice(SettingsStateSlice).settings_service
    return MemoryService(
        memory_backend=backend,
        settings_service=settings_service,
    )


# --- handlers -------------------------------------------------------------


async def _memory_start_fine_tune(
    *,
    app_state: Any,
    arguments: dict[str, Any],
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
    except MemoryError, RecursionError:
        raise
    except FineTuneRunActiveError as exc:
        # The orchestrator raises this when another run is already
        # active; surface it as a conflict so callers get a typed
        # recovery path instead of a generic handler error.
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="conflict")
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_ADMIN_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=actor_id(resolved_actor),
        reason=reason,
        target_id=run.id,
    )
    return ok(data=run.model_dump(mode="json"))


async def _memory_resume_fine_tune(
    *,
    app_state: Any,
    arguments: dict[str, Any],
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
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_ADMIN_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=actor_id(resolved_actor),
        reason=reason,
        target_id=run.id,
    )
    return ok(data=run.model_dump(mode="json"))


async def _memory_get_fine_tune_status(
    *,
    app_state: Any,
    arguments: dict[str, Any],
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
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=status.model_dump(mode="json"))


async def _memory_cancel_fine_tune(
    *,
    app_state: Any,
    arguments: dict[str, Any],
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
    except Exception as exc:
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
    app_state: Any,
    arguments: dict[str, Any],
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
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=result.model_dump(mode="json"))


async def _memory_list_checkpoints(
    *,
    app_state: Any,
    arguments: dict[str, Any],
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
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(checkpoints), pagination=meta)


async def _memory_deploy_checkpoint(
    *,
    app_state: Any,
    arguments: dict[str, Any],
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
    except Exception as exc:
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
    app_state: Any,
    arguments: dict[str, Any],
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
    except Exception as exc:
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
    app_state: Any,
    arguments: dict[str, Any],
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
    except Exception as exc:
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


async def _memory_list_runs(
    *,
    app_state: Any,
    arguments: dict[str, Any],
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
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(runs), pagination=meta)


async def _memory_get_active_embedder(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
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
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=snap.model_dump(mode="json"))


MEMORY_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    copy.deepcopy(
        {
            "synthorg_memory_start_fine_tune": _memory_start_fine_tune,
            "synthorg_memory_resume_fine_tune": _memory_resume_fine_tune,
            "synthorg_memory_get_fine_tune_status": _memory_get_fine_tune_status,
            "synthorg_memory_cancel_fine_tune": _memory_cancel_fine_tune,
            "synthorg_memory_run_preflight": _memory_run_preflight,
            "synthorg_memory_list_checkpoints": _memory_list_checkpoints,
            "synthorg_memory_deploy_checkpoint": _memory_deploy_checkpoint,
            "synthorg_memory_rollback_checkpoint": _memory_rollback_checkpoint,
            "synthorg_memory_delete_checkpoint": _memory_delete_checkpoint,
            "synthorg_memory_list_runs": _memory_list_runs,
            "synthorg_memory_get_active_embedder": _memory_get_active_embedder,
            "synthorg_memory_delete_entry": _memory_delete_entry,
        },
    ),
)
