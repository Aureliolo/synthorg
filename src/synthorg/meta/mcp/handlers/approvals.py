"""Approval domain MCP handlers.

Shims the 5 approval tools onto ``app_state.approval_store`` (the
in-memory + optionally persisted ``ApprovalStore`` conforming to
``ApprovalStoreProtocol``).  Handlers are thin adapters: they parse
arguments, call the store, wrap the result in the common envelope.

Destructive ops
---------------
``synthorg_approvals_reject`` is destructive and enforces
``confirm=True`` + non-blank ``reason`` + non-``None`` ``actor`` via
``require_destructive_guardrails`` before mutating state.  It emits
``MCP_DESTRUCTIVE_OP_EXECUTED`` at INFO exactly once per successful
rejection.  Create and approve are non-destructive writes and only
need an actor (to populate ``requested_by`` / ``decided_by``).
"""

import copy
from collections.abc import Mapping  # noqa: TC003 -- PEP 649 annotation
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from synthorg.api.errors import ConflictError
from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
    invalid_argument,
)
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,  # noqa: TC001 -- PEP 649 annotation
)
from synthorg.meta.mcp.handlers.common import (
    dump_many,
    err,
    ok,
    paginate_sequence,
    require_destructive_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import coerce_pagination, require_arg
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.mcp import (
    MCP_DESTRUCTIVE_OP_EXECUTED,
    MCP_HANDLER_ARGUMENT_INVALID,
    MCP_HANDLER_GUARDRAIL_VIOLATED,
    MCP_HANDLER_INVOKE_FAILED,
    MCP_HANDLER_INVOKE_SUCCESS,
)

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)


class _NotFoundError(LookupError):
    """Handler-local not-found signal.

    Raised inside the try block so the ``err()`` envelope picks up
    ``domain_code=not_found`` without taking a dependency on Litestar's
    ``NotFoundError`` (that one would trigger 404 handling in HTTP
    paths; MCP has no HTTP layer).
    """

    domain_code = "not_found"


class _ConflictError(RuntimeError):
    """Handler-local conflict signal (approve/reject race)."""

    domain_code = "conflict"


# --- argument coercion helpers ---------------------------------------------


_TY_STRING = "string"
_TY_NON_BLANK = "non-blank string"
_TY_STATUS = "ApprovalStatus"
_TY_RISK = "ApprovalRiskLevel"
_TY_AGENT = "identified agent"
_ARG_STATUS = "status"
_ARG_ACTOR = "actor"
_ARG_TITLE = "title"
_ARG_COMMENT = "comment"
_ARG_ACTION_TYPE = "action_type"
_ARG_RISK_LEVEL = "risk_level"


def _coerce_status(raw: Any) -> ApprovalStatus | None:
    """Map a string argument to ``ApprovalStatus`` or raise."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise invalid_argument(_ARG_STATUS, _TY_STRING)
    try:
        return ApprovalStatus(raw)
    except ValueError as exc:
        raise invalid_argument(_ARG_STATUS, _TY_STATUS) from exc


def _coerce_risk(raw: Any, *, field: str = "risk_level") -> ApprovalRiskLevel | None:
    """Map a string argument to ``ApprovalRiskLevel`` or raise."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise invalid_argument(field, _TY_STRING)
    try:
        return ApprovalRiskLevel(raw)
    except ValueError as exc:
        raise invalid_argument(field, _TY_RISK) from exc


def _require_non_blank(arguments: dict[str, Any], key: str) -> str:
    """Extract a non-blank string argument, stripped of surrounding whitespace.

    Normalising at the boundary keeps downstream lookups consistent --
    an ``approval_id`` like ``"  approval-123  "`` would otherwise fail
    the store's exact-match lookup.
    """
    raw = require_arg(arguments, key, str)
    if not raw.strip():
        raise invalid_argument(key, _TY_NON_BLANK)
    return raw.strip()


def _actor_id(actor: Any) -> str | None:
    """Return a stable audit identifier for ``actor``.

    Prefers ``actor.id`` (a ``UUID`` that never changes over the
    agent's lifetime) so ``decided_by`` audit records stay consistent
    even when the display name is later edited.  Falls back to
    ``actor.name`` only when id is absent.
    """
    if actor is None:
        return None
    agent_id = getattr(actor, "id", None)
    if agent_id is not None:
        return str(agent_id)
    name = getattr(actor, "name", None)
    return name if isinstance(name, str) and name else None


def _log_failed(tool: str, exc: Exception) -> None:
    logger.warning(
        MCP_HANDLER_INVOKE_FAILED,
        tool_name=tool,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )


def _log_invalid(tool: str, exc: Exception) -> None:
    logger.warning(
        MCP_HANDLER_ARGUMENT_INVALID,
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


def _required_actor_id(actor: Any) -> str:
    """Return the actor's stable id (or name fallback) or raise."""
    name = _actor_id(actor)
    if name is None:
        raise invalid_argument(_ARG_ACTOR, _TY_AGENT)
    return name


# --- handlers --------------------------------------------------------------


async def _list_approvals(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handler: ``synthorg_approvals_list``."""
    tool = "synthorg_approvals_list"

    # Arg parsing (may raise ArgumentValidationError).
    try:
        status = _coerce_status(arguments.get("status"))
        risk = _coerce_risk(arguments.get("risk_level"))
        action_type_raw = arguments.get("action_type")
        action_type: str | None = None
        if action_type_raw is not None:
            if not isinstance(action_type_raw, str) or not action_type_raw.strip():
                raise invalid_argument(_ARG_ACTION_TYPE, _TY_NON_BLANK)
            action_type = action_type_raw.strip()
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)

    # Service call (isolated so domain errors log at WARNING).  Argument
    # validation is already complete above, so any failure here is a
    # service-layer problem -- a single ``except Exception`` is enough.
    try:
        items = await app_state.approval_store.list_items(
            status=status,
            risk_level=risk,
            action_type=action_type,
        )
        page, meta = paginate_sequence(items, offset=offset, limit=limit)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)

    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(page), pagination=meta)


async def _get_approval(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handler: ``synthorg_approvals_get``."""
    tool = "synthorg_approvals_get"

    try:
        approval_id = _require_non_blank(arguments, "approval_id")
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)

    try:
        item = await app_state.approval_store.get(approval_id)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)

    if item is None:
        missing = _NotFoundError(f"Approval {approval_id!r} not found")
        _log_failed(tool, missing)
        return err(missing)

    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=item.model_dump(mode="json"))


async def _create_approval(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Handler: ``synthorg_approvals_create``."""
    tool = "synthorg_approvals_create"

    try:
        requested_by = _required_actor_id(actor)
        action_type = _require_non_blank(arguments, "action_type")
        description = _require_non_blank(arguments, "description")
        title_raw = arguments.get("title")
        if title_raw is None:
            title = description[:80]
        elif not isinstance(title_raw, str) or not title_raw.strip():
            raise invalid_argument(_ARG_TITLE, _TY_NON_BLANK)
        else:
            title = title_raw
        risk = _coerce_risk(arguments.get("risk_level", "medium"))
        if risk is None:
            raise invalid_argument(_ARG_RISK_LEVEL, _TY_RISK)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)

    now = datetime.now(UTC)
    item = ApprovalItem(
        id=f"approval-{uuid4().hex}",
        action_type=action_type,
        title=title,
        description=description,
        requested_by=requested_by,
        risk_level=risk,
        created_at=now,
    )
    try:
        await app_state.approval_store.add(item)
    except ConflictError as exc:
        _log_failed(tool, exc)
        return err(exc, domain_code="conflict")
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)

    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=item.model_dump(mode="json"))


async def _decide(
    *,
    app_state: Any,
    approval_id: str,
    actor: Any,
    target: ApprovalStatus,
    reason: str | None,
) -> ApprovalItem:
    """Shared approve/reject finalisation.

    Fetches the current item, stamps decision fields, and writes via
    ``save_if_pending`` so a concurrent decision cannot race us past
    first-writer-wins.  When ``save_if_pending`` returns ``None`` we
    re-read the approval to distinguish *gone* (``_NotFoundError``) from
    *raced to a new state* (``_ConflictError``) -- a silent collapse to
    "conflict" misleads callers when the item was actually deleted or
    expired between the fetch and the write.

    Raises:
        _NotFoundError: Approval id does not exist or was removed.
        _ConflictError: Item already decided or in-flight save.
        ArgumentValidationError: Actor is missing a decidable name.
    """
    decided_by = _required_actor_id(actor)
    existing = await app_state.approval_store.get(approval_id)
    if existing is None:
        msg = f"Approval {approval_id!r} not found"
        raise _NotFoundError(msg)
    if existing.status != ApprovalStatus.PENDING:
        msg = f"Approval {approval_id!r} is {existing.status.value!s}, not pending"
        raise _ConflictError(msg)
    now = datetime.now(UTC)
    updated = existing.model_copy(
        update={
            "status": target,
            "decided_at": now,
            "decided_by": decided_by,
            "decision_reason": reason,
        },
    )
    saved: ApprovalItem | None = await app_state.approval_store.save_if_pending(
        updated,
    )
    if saved is None:
        current = await app_state.approval_store.get(approval_id)
        if current is None:
            msg = f"Approval {approval_id!r} was removed before decision"
            raise _NotFoundError(msg)
        msg = (
            f"Approval {approval_id!r} was decided concurrently "
            f"(now {current.status.value!s})"
        )
        raise _ConflictError(msg)
    return saved


async def _approve(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Handler: ``synthorg_approvals_approve``."""
    tool = "synthorg_approvals_approve"

    try:
        approval_id = _require_non_blank(arguments, "approval_id")
        comment = arguments.get("comment")
        if comment is not None and not isinstance(comment, str):
            raise invalid_argument(_ARG_COMMENT, _TY_STRING)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)

    try:
        saved = await _decide(
            app_state=app_state,
            approval_id=approval_id,
            actor=actor,
            target=ApprovalStatus.APPROVED,
            reason=comment,
        )
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except _NotFoundError as exc:
        _log_failed(tool, exc)
        return err(exc)
    except _ConflictError as exc:
        _log_failed(tool, exc)
        return err(exc)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)

    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=saved.model_dump(mode="json"))


async def _reject(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Handler: ``synthorg_approvals_reject`` (destructive).

    Guardrails (via ``require_destructive_guardrails``): ``confirm=True``,
    non-blank ``reason``, non-``None`` ``actor``.
    """
    tool = "synthorg_approvals_reject"

    try:
        approval_id = _require_non_blank(arguments, "approval_id")
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)

    try:
        reason, _ = require_destructive_guardrails(arguments, actor)
    except GuardrailViolationError as exc:
        _log_guardrail(tool, exc)
        return err(exc)

    try:
        saved = await _decide(
            app_state=app_state,
            approval_id=approval_id,
            actor=actor,
            target=ApprovalStatus.REJECTED,
            reason=reason,
        )
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        # Covers _NotFoundError, _ConflictError, and any other service-layer
        # failure.  The ``err()`` envelope picks up ``domain_code`` off the
        # handler-local errors automatically.
        _log_failed(tool, exc)
        return err(exc)

    # Emit both the handler-success telemetry *and* the destructive-op
    # audit event so "all handler successes" dashboards still see this
    # path and the audit trail carries full attribution.
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_DESTRUCTIVE_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=_actor_id(actor),
        reason=reason,
        target_id=approval_id,
    )
    return ok(data=saved.model_dump(mode="json"))


APPROVAL_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    copy.deepcopy(
        {
            "synthorg_approvals_list": _list_approvals,
            "synthorg_approvals_get": _get_approval,
            "synthorg_approvals_create": _create_approval,
            "synthorg_approvals_approve": _approve,
            "synthorg_approvals_reject": _reject,
        },
    ),
)
