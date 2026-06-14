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

from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import uuid4

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.approval.state import approval_store_of
from synthorg.core.agent import (
    AgentIdentity,
)
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ConflictError
from synthorg.meta.mcp.domains._simple_args import (
    ApprovalsApproveArgs,
    ApprovalsCreateArgs,
    ApprovalsGetArgs,
    ApprovalsListArgs,
    ApprovalsRejectArgs,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,
)
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import (
    dump_many,
    err,
    ok,
    paginate_sequence,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    actor_id,
    require_actor_id,
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
    """
    tool = "synthorg_approvals_list"

    try:
        args = typed_args(arguments, ApprovalsListArgs)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    status = ApprovalStatus(args.status) if args.status is not None else None
    risk = ApprovalRiskLevel(args.risk_level) if args.risk_level is not None else None

    # Service call (isolated so domain errors log at WARNING).  Argument
    # validation is already complete above, so any failure here is a
    # service-layer problem -- a single ``except Exception`` is enough.
    try:
        items = await approval_store_of(app_state).list_items(
            status=status,
            risk_level=risk,
            action_type=args.action_type,
        )
        page, meta = paginate_sequence(items, offset=args.offset, limit=args.limit)
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
        approval_id = typed_args(arguments, ApprovalsGetArgs).approval_id
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
        args = typed_args(arguments, ApprovalsCreateArgs)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    title = args.title if args.title is not None else args.description[:80]
    risk = ApprovalRiskLevel(args.risk_level)

    now = datetime.now(UTC)
    item = ApprovalItem(
        id=uuid4(),
        action_type=args.action_type,
        title=title,
        description=args.description,
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
        args = typed_args(arguments, ApprovalsApproveArgs)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    try:
        saved = await _decide(
            app_state=app_state,
            approval_id=args.approval_id,
            actor=actor,
            target=ApprovalStatus.APPROVED,
            reason=args.comment,
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
        approval_id = typed_args(arguments, ApprovalsRejectArgs).approval_id
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
    {
        "synthorg_approvals_list": _list_approvals,
        "synthorg_approvals_get": _get_approval,
        "synthorg_approvals_create": _create_approval,
        "synthorg_approvals_approve": _approve,
        "synthorg_approvals_reject": _reject,
    },
)
