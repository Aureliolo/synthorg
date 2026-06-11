"""Approval domain MCP handlers.

Shims the 5 approval tools onto the approval store (the in-memory +
optionally persisted ``ApprovalStore`` conforming to
``ApprovalStoreProtocol``) reached via the ``ApprovalStateSlice``.
Handlers are thin adapters: they parse
arguments, call the store, wrap the result in the common envelope.

Destructive ops
---------------
``synthorg_approvals_reject`` is destructive and enforces
``confirm=True`` + non-blank ``reason`` + non-``None`` ``actor`` via
``require_admin_guardrails`` before mutating state.  It emits
``MCP_ADMIN_OP_EXECUTED`` at INFO exactly once per successful
rejection.  Create and approve are non-destructive writes and only
need an actor (to populate ``requested_by`` / ``decided_by``).
"""

import copy
from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from uuid import uuid4

from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.approval.state import approval_store_of
from synthorg.core.agent import (
    AgentIdentity,
)
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ConflictError
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,
)
from synthorg.meta.mcp.handlers.common import (
    dump_many,
    err,
    ok,
    paginate_sequence,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    actor_id,
    coerce_pagination,
    require_actor_id,
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

logger = get_logger(__name__)


class _NotFoundError(
    LookupError,
):  # lint-allow: domain-error-hierarchy -- MCP handler-local; no HTTP layer
    """Handler-local not-found signal.

    Raised inside the try block so the ``err()`` envelope picks up
    ``domain_code=not_found`` without taking a dependency on Litestar's
    ``NotFoundError`` (that one would trigger 404 handling in HTTP
    paths; MCP has no HTTP layer).
    """

    domain_code = "not_found"


class _ConflictError(
    RuntimeError,
):  # lint-allow: domain-error-hierarchy -- MCP handler-local; no HTTP layer
    """Handler-local conflict signal (approve/reject race)."""

    domain_code = "conflict"


# --- argument coercion helpers ---------------------------------------------


_TY_STRING = "string"
_TY_NON_BLANK = "non-blank string"
_TY_STATUS = "ApprovalStatus"
_TY_RISK = "ApprovalRiskLevel"
_ARG_STATUS = "status"
_ARG_TITLE = "title"
_ARG_COMMENT = "comment"
_ARG_ACTION_TYPE = "action_type"
_ARG_RISK_LEVEL = "risk_level"


def _coerce_status(raw: object) -> ApprovalStatus | None:
    """Map a string argument to ``ApprovalStatus`` or raise.

    Returns:
        The ``ApprovalStatus`` value when present, ``None`` otherwise.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ArgumentValidationError(_ARG_STATUS, _TY_STRING)
    try:
        return ApprovalStatus(raw)
    except ValueError as exc:
        raise ArgumentValidationError(_ARG_STATUS, _TY_STATUS) from exc


def _coerce_risk(raw: object, *, field: str = "risk_level") -> ApprovalRiskLevel | None:
    """Map a string argument to ``ApprovalRiskLevel`` or raise.

    Returns:
        The ``ApprovalRiskLevel`` value when present, ``None`` otherwise.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ArgumentValidationError(field, _TY_STRING)
    try:
        return ApprovalRiskLevel(raw)
    except ValueError as exc:
        raise ArgumentValidationError(field, _TY_RISK) from exc


# --- handlers --------------------------------------------------------------


async def _list_approvals(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handler: ``synthorg_approvals_list``.

    Returns:
        Resulting string.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    tool = "synthorg_approvals_list"

    # Arg parsing (may raise ArgumentValidationError).
    try:
        status = _coerce_status(arguments.get("status"))
        risk = _coerce_risk(arguments.get("risk_level"))
        action_type_raw = arguments.get("action_type")
        action_type: str | None = None
        if action_type_raw is not None:
            if not isinstance(action_type_raw, str) or not action_type_raw.strip():
                raise ArgumentValidationError(_ARG_ACTION_TYPE, _TY_NON_BLANK)
            action_type = action_type_raw.strip()
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    # Service call (isolated so domain errors log at WARNING).  Argument
    # validation is already complete above, so any failure here is a
    # service-layer problem -- a single ``except Exception`` is enough.
    try:
        items = await approval_store_of(app_state).list_items(
            status=status,
            risk_level=risk,
            action_type=action_type,
        )
        page, meta = paginate_sequence(items, offset=offset, limit=limit)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)

    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(page), pagination=meta)


async def _get_approval(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handler: ``synthorg_approvals_get``.

    Returns:
        Resulting string.
    """
    tool = "synthorg_approvals_get"

    try:
        approval_id = require_non_blank(arguments, "approval_id")
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    try:
        item = await approval_store_of(app_state).get(approval_id)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)

    if item is None:
        missing = _NotFoundError(f"Approval {approval_id!r} not found")
        log_handler_invoke_failed(tool, missing)
        return err(missing)

    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=item.model_dump(mode="json"))


async def _create_approval(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handler: ``synthorg_approvals_create``.

    Returns:
        Resulting string.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    tool = "synthorg_approvals_create"

    try:
        requested_by = require_actor_id(actor)
        action_type = require_non_blank(arguments, "action_type")
        description = require_non_blank(arguments, "description")
        title_raw = arguments.get("title")
        if title_raw is None:
            title = description[:80]
        elif not isinstance(title_raw, str) or not title_raw.strip():
            raise ArgumentValidationError(_ARG_TITLE, _TY_NON_BLANK)
        else:
            title = title_raw
        risk = _coerce_risk(arguments.get("risk_level", "medium"))
        if risk is None:
            raise ArgumentValidationError(_ARG_RISK_LEVEL, _TY_RISK)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    now = datetime.now(UTC)
    item = ApprovalItem(
        id=uuid4(),
        action_type=action_type,
        title=title,
        description=description,
        requested_by=requested_by,
        risk_level=risk,
        created_at=now,
    )
    try:
        await approval_store_of(app_state).add(item)
    except ConflictError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="conflict")
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)

    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=item.model_dump(mode="json"))


async def _decide(
    *,
    app_state: AppState,
    approval_id: str,
    actor: AgentIdentity | None,
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

    Returns:
        ``ApprovalItem`` instance.
    """
    decided_by = require_actor_id(actor)
    store = approval_store_of(app_state)
    existing = await store.get(approval_id)
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
    saved: ApprovalItem | None = await store.save_if_pending(
        updated,
    )
    if saved is None:
        current = await store.get(approval_id)
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
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handler: ``synthorg_approvals_approve``.

    Returns:
        Resulting string.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    tool = "synthorg_approvals_approve"

    try:
        approval_id = require_non_blank(arguments, "approval_id")
        comment = arguments.get("comment")
        if comment is not None and not isinstance(comment, str):
            raise ArgumentValidationError(_ARG_COMMENT, _TY_STRING)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
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
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except _NotFoundError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    except _ConflictError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)

    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=saved.model_dump(mode="json"))


async def _reject(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handler: ``synthorg_approvals_reject`` (destructive).

    Guardrails (via ``require_admin_guardrails``): ``confirm=True``,
    non-blank ``reason``, non-``None`` ``actor``.

    Returns:
        Resulting string.
    """
    tool = "synthorg_approvals_reject"

    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        approval_id = require_non_blank(arguments, "approval_id")
        saved = await _decide(
            app_state=app_state,
            approval_id=approval_id,
            actor=resolved_actor,
            target=ApprovalStatus.REJECTED,
            reason=reason,
        )
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        # Covers _NotFoundError, _ConflictError, and any other service-layer
        # failure.  The ``err()`` envelope picks up ``domain_code`` off the
        # handler-local errors automatically.
        log_handler_invoke_failed(tool, exc)
        return err(exc)

    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_ADMIN_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=actor_id(resolved_actor),
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
