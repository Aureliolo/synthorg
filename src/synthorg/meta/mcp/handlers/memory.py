"""Memory domain MCP handlers (fine-tune checkpoints + runs).

Wires 11 tools through :class:`MemoryService`. The service is
injected via ``app_state.memory_service`` by the application
bootstrap; handlers route through that facade exclusively and never
reach into ``app_state.persistence.*`` directly (CLAUDE.md
persistence-boundary rule).

Backend-unsupported routing. :class:`BackendUnsupportedError` is
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

Destructive ops. ``cancel_fine_tune``, ``rollback_checkpoint``, and
``delete_checkpoint`` enforce the guardrail triple at the handler
boundary and emit :data:`MCP_DESTRUCTIVE_OP_EXECUTED` on success.
"""

import copy
from collections.abc import Mapping  # noqa: TC003 -- PEP 649 annotation
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from synthorg.core.types import NotBlankStr
from synthorg.memory.embedding.fine_tune_models import FineTuneExecutionConfig
from synthorg.memory.fine_tune_plan import (
    BackendUnsupportedError,
    FineTunePlan,
)
from synthorg.memory.service import (
    CheckpointNotFoundError,
    CheckpointRollbackCorruptError,
    CheckpointRollbackUnavailableError,
    FineTuneRunNotFoundError,
    FineTuneRunNotResumableError,
    MemoryService,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
    invalid_argument,
)
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,  # noqa: TC001 -- PEP 649 annotation
)
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    dump_many,
    err,
    not_supported,
    ok,
    require_destructive_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import coerce_pagination
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.mcp import (
    MCP_DESTRUCTIVE_OP_EXECUTED,
    MCP_HANDLER_ARGUMENT_INVALID,
    MCP_HANDLER_GUARDRAIL_VIOLATED,
    MCP_HANDLER_INVOKE_FAILED,
    MCP_HANDLER_INVOKE_SUCCESS,
)
from synthorg.persistence.errors import PersistenceConnectionError, QueryError

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)


_TY_NON_BLANK = "non-blank string"
_ARG_CHECKPOINT_ID = "checkpoint_id"
_ARG_RUN_ID = "run_id"


def _log_invalid(tool: str, exc: Exception) -> None:
    logger.warning(
        MCP_HANDLER_ARGUMENT_INVALID,
        tool_name=tool,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )


def _log_failed(tool: str, exc: Exception) -> None:
    logger.warning(
        MCP_HANDLER_INVOKE_FAILED,
        tool_name=tool,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )


def _log_guardrail(tool: str, exc: GuardrailViolationError) -> None:
    logger.warning(
        MCP_HANDLER_GUARDRAIL_VIOLATED,
        tool_name=tool,
        violation=exc.violation,
    )


def _actor_id(actor: Any) -> str | None:
    """Return a stable audit identifier for ``actor`` (prefers ``.id``)."""
    if actor is None:
        return None
    agent_id = getattr(actor, "id", None)
    if agent_id is not None:
        return str(agent_id)
    name = getattr(actor, "name", None)
    return name if isinstance(name, str) and name else None


def _require_non_blank(arguments: dict[str, Any], key: str) -> str:
    """Extract a non-blank string argument, stripped of surrounding whitespace."""
    raw = arguments.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise invalid_argument(key, _TY_NON_BLANK)
    return raw.strip()


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

    Every failure mode raises :class:`BackendUnsupportedError` so the
    calling handler returns a uniform ``not_supported`` envelope:

    * No wired service **and** the raw backend is absent / doesn't expose
      fine-tune repos.
    * The backend's fine-tune property raises ``NotImplementedError``
      (legacy / partial backend).
    * The backend is not yet connected and the property's
      ``_require_connected`` guard raises
      :class:`~synthorg.persistence.errors.PersistenceConnectionError`.

    Raises:
        BackendUnsupportedError: In any of the above cases.
    """
    if getattr(app_state, "has_memory_service", False):
        attached: MemoryService = app_state.memory_service
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
    backend = getattr(app_state, "persistence", None)
    if backend is None:
        raise BackendUnsupportedError(_WHY_MEMORY_SERVICE_NOT_WIRED)
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
        raise BackendUnsupportedError(_WHY_BACKEND_NO_FINE_TUNE) from exc
    has_settings = getattr(app_state, "has_settings_service", False)
    return MemoryService(
        checkpoint_repo=checkpoint_repo,
        run_repo=run_repo,
        settings_service=(
            getattr(app_state, "settings_service", None) if has_settings else None
        ),
    )


# --- handlers -------------------------------------------------------------


async def _memory_start_fine_tune(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_memory_start_fine_tune"
    try:
        plan = _parse_fine_tune_plan(arguments)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    try:
        service = _service(app_state)
        run = await service.start_fine_tune(plan)
    except BackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    except MemoryError, RecursionError:
        raise
    except RuntimeError as exc:
        # ``MemoryService.start_fine_tune`` raises ``RuntimeError``
        # when another run is already active; surface that as a
        # conflict so callers get a typed recovery path instead of
        # a generic handler error.
        _log_failed(tool, exc)
        return err(exc, domain_code="conflict")
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=run.model_dump(mode="json"))


async def _memory_resume_fine_tune(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_memory_resume_fine_tune"
    try:
        run_id = _require_non_blank(arguments, _ARG_RUN_ID)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    try:
        service = _service(app_state)
        run = await service.resume_fine_tune(NotBlankStr(run_id))
    except BackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    except RuntimeError as exc:
        # Another run is already active -- same ``conflict`` mapping
        # as :func:`_memory_start_fine_tune`.
        _log_failed(tool, exc)
        return err(exc, domain_code="conflict")
    except (FineTuneRunNotFoundError, FineTuneRunNotResumableError) as exc:
        # Typed exceptions carry their own ``domain_code`` class
        # attribute (``not_found`` / ``conflict``), so ``err(exc)``
        # picks up the right wire contract without regex-matching
        # the message -- any future wording change in the
        # orchestrator won't reclassify the failure.
        _log_failed(tool, exc)
        return err(exc)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=run.model_dump(mode="json"))


async def _memory_get_fine_tune_status(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_memory_get_fine_tune_status"
    run_id_raw = arguments.get(_ARG_RUN_ID)
    run_id: NotBlankStr | None = None
    if run_id_raw is not None:
        if not isinstance(run_id_raw, str) or not run_id_raw.strip():
            exc = invalid_argument(_ARG_RUN_ID, _TY_NON_BLANK)
            _log_invalid(tool, exc)
            return err(exc)
        run_id = NotBlankStr(run_id_raw.strip())
    try:
        service = _service(app_state)
        status = await service.get_fine_tune_status(run_id)
    except BackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    except ValueError as exc:
        _log_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=status.model_dump(mode="json"))


async def _memory_cancel_fine_tune(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    tool = "synthorg_memory_cancel_fine_tune"
    try:
        reason, resolved_actor = require_destructive_guardrails(
            arguments,
            actor,
        )
    except GuardrailViolationError as exc:
        _log_guardrail(tool, exc)
        return err(exc)
    try:
        service = _service(app_state)
        target_id = await service.cancel_fine_tune()
    except BackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    # Only emit the destructive-op audit when something was actually
    # cancelled. A ``None`` target means the orchestrator had no active
    # run, and emitting ``MCP_DESTRUCTIVE_OP_EXECUTED`` with
    # ``target_id=None`` would plant a false no-op entry in the audit
    # trail. Operators investigating a cancellation should never see a
    # record for a cancel that did not happen.
    if target_id is not None:
        logger.info(
            MCP_DESTRUCTIVE_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=_actor_id(resolved_actor),
            reason=reason,
            target_id=target_id,
        )
    return ok()


async def _memory_run_preflight(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_memory_run_preflight"
    try:
        plan = _parse_fine_tune_plan(arguments)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    try:
        service = _service(app_state)
        result = await service.run_preflight(plan)
    except BackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=result.model_dump(mode="json"))


async def _memory_list_checkpoints(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_memory_list_checkpoints"
    try:
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    try:
        service = _service(app_state)
    except BackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    try:
        checkpoints, total = await service.list_checkpoints(
            limit=limit,
            offset=offset,
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(checkpoints), pagination=meta)


async def _memory_deploy_checkpoint(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_memory_deploy_checkpoint"
    try:
        checkpoint_id = _require_non_blank(arguments, _ARG_CHECKPOINT_ID)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    try:
        service = _service(app_state)
    except BackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    try:
        cp = await service.deploy_checkpoint(checkpoint_id)
    except CheckpointNotFoundError as exc:
        _log_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except QueryError as exc:
        # Persistence-layer failure during deploy (e.g. the checkpoint
        # was activated but the re-read failed) -- surface as
        # ``conflict`` so callers distinguish from internal errors.
        _log_failed(tool, exc)
        return err(exc, domain_code="conflict")
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=cp.model_dump(mode="json"))


async def _memory_rollback_checkpoint(  # noqa: PLR0911
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    tool = "synthorg_memory_rollback_checkpoint"
    try:
        checkpoint_id = _require_non_blank(arguments, _ARG_CHECKPOINT_ID)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    try:
        reason, resolved_actor = require_destructive_guardrails(
            arguments,
            actor,
        )
    except GuardrailViolationError as exc:
        _log_guardrail(tool, exc)
        return err(exc)
    try:
        service = _service(app_state)
    except BackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    try:
        cp = await service.rollback_checkpoint(checkpoint_id)
    except CheckpointNotFoundError as exc:
        _log_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except (
        CheckpointRollbackUnavailableError,
        CheckpointRollbackCorruptError,
    ) as exc:
        _log_failed(tool, exc)
        return err(exc, domain_code="conflict")
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_DESTRUCTIVE_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=_actor_id(resolved_actor),
        reason=reason,
        target_id=checkpoint_id,
    )
    return ok(data=cp.model_dump(mode="json"))


async def _memory_delete_checkpoint(  # noqa: PLR0911
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    tool = "synthorg_memory_delete_checkpoint"
    try:
        checkpoint_id = _require_non_blank(arguments, _ARG_CHECKPOINT_ID)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    try:
        reason, resolved_actor = require_destructive_guardrails(
            arguments,
            actor,
        )
    except GuardrailViolationError as exc:
        _log_guardrail(tool, exc)
        return err(exc)

    try:
        service = _service(app_state)
    except BackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    try:
        await service.delete_checkpoint(checkpoint_id)
    except CheckpointNotFoundError as exc:
        _log_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except QueryError as exc:
        # Active-checkpoint / domain-rule violation -- surface as
        # ``conflict`` so callers can distinguish from internal errors.
        _log_failed(tool, exc)
        return err(exc, domain_code="conflict")
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)

    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_DESTRUCTIVE_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=_actor_id(resolved_actor),
        reason=reason,
        target_id=checkpoint_id,
    )
    return ok()


async def _memory_list_runs(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_memory_list_runs"
    try:
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    try:
        service = _service(app_state)
        runs, total = await service.list_runs(limit=limit, offset=offset)
    except BackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
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
    tool = "synthorg_memory_get_active_embedder"
    try:
        service = _service(app_state)
        snap = await service.get_active_embedder()
    except BackendUnsupportedError as exc:
        return not_supported(tool, str(exc))
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=snap.model_dump(mode="json"))


_OPTIONAL_STR_KEYS: tuple[str, ...] = (
    "base_model",
    "output_dir",
    "resume_run_id",
)
_OPTIONAL_INT_KEYS: tuple[str, ...] = ("epochs", "top_k", "batch_size")
_OPTIONAL_FLOAT_KEYS: tuple[str, ...] = (
    "learning_rate",
    "temperature",
    "validation_split",
)
_ARG_EXECUTION = "execution"
_TY_EXECUTION_OR_NULL = "object or null"
_TY_EXECUTION_SHAPE = "valid FineTuneExecutionConfig shape"


def _collect_optional_strings(
    arguments: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    for key in _OPTIONAL_STR_KEYS:
        if key not in arguments:
            continue
        raw = arguments[key]
        if raw is None:
            continue
        if not isinstance(raw, str) or not raw.strip():
            # Reject present-but-malformed values (e.g. ``""`` or a
            # non-string) instead of silently dropping them -- otherwise
            # ``resume_run_id=""`` would become a fresh fine-tune rather
            # than an ``invalid_argument`` response.
            raise invalid_argument(key, _TY_NON_BLANK)
        payload[key] = NotBlankStr(raw.strip())


def _collect_optional_ints(
    arguments: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    for key in _OPTIONAL_INT_KEYS:
        raw = arguments.get(key)
        if raw is None:
            continue
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise invalid_argument(key, "positive int")
        payload[key] = raw


def _collect_optional_floats(
    arguments: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    for key in _OPTIONAL_FLOAT_KEYS:
        raw = arguments.get(key)
        if raw is None:
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise invalid_argument(key, "positive float")
        payload[key] = float(raw)


def _collect_optional_execution(
    arguments: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """Translate the optional ``execution`` object into a typed sub-model.

    Silently dropping the field would let callers send runner / backend
    overrides and still get ``"status": "ok"`` back while the service
    runs with defaults -- a hard-to-debug contract hole. Instead, any
    present-but-not-null value must be a JSON object that validates
    against :class:`FineTuneExecutionConfig`, else we return an
    ``invalid_argument`` envelope with the nested field name.
    """
    raw = arguments.get(_ARG_EXECUTION)
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise invalid_argument(_ARG_EXECUTION, _TY_EXECUTION_OR_NULL)
    try:
        payload[_ARG_EXECUTION] = FineTuneExecutionConfig(**raw)
    except Exception as exc:
        raise invalid_argument(_ARG_EXECUTION, _TY_EXECUTION_SHAPE) from exc


def _parse_fine_tune_plan(arguments: dict[str, Any]) -> FineTunePlan:
    """Build a :class:`FineTunePlan` from MCP arguments with typed errors."""
    source_dir = _require_non_blank(arguments, "source_dir")
    payload: dict[str, Any] = {"source_dir": NotBlankStr(source_dir)}
    _collect_optional_strings(arguments, payload)
    _collect_optional_ints(arguments, payload)
    _collect_optional_floats(arguments, payload)
    _collect_optional_execution(arguments, payload)
    try:
        return FineTunePlan(**payload)
    except Exception as exc:
        arg_name = "plan"
        expected = "valid FineTunePlan shape"
        raise invalid_argument(arg_name, expected) from exc


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
        },
    ),
)
