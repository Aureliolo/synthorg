"""Human escalation queue REST endpoints.

Operator-facing surface for listing pending conflict escalations and
submitting decisions back to the awaiting
:class:`HumanEscalationResolver`.

All endpoints carry per-operation rate limits so a runaway dashboard
client cannot flood the queue.  Error responses use the shared RFC
9457 handlers registered by :mod:`synthorg.api.exception_handlers`.
"""

from typing import Annotated, Final

from litestar import Controller, get, post
from litestar.datastructures import State  # noqa: TC002
from litestar.params import QueryParameter
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.cursor import decode_cursor, encode_cursor
from synthorg.api.dto import ApiResponse, PaginatedResponse, PaginationMeta
from synthorg.api.guards import require_approval_roles, require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
)
from synthorg.api.path_params import PathId  # noqa: TC001
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.communication.conflict_resolution.escalation.models import (
    Escalation,
    EscalationDecision,
    EscalationStatus,
)
from synthorg.communication.errors import EscalationDecisionError
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.actor_context import require_actor
from synthorg.core.domain_errors import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.conflict import (
    CONFLICT_ESCALATION_CANCELLED,
    CONFLICT_ESCALATION_RESOLVED,
)
from synthorg.observability.metrics_hub import record_escalation_outcome

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


# ── Request / response DTOs ─────────────────────────────────────


class EscalationResponse(BaseModel):
    """Escalation row enriched for the dashboard."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    escalation: Escalation
    conflict_id: NotBlankStr
    status: EscalationStatus


class SubmitDecisionRequest(BaseModel):
    """Body for ``POST /escalations/{id}/decision``.

    Attributes:
        decision: Tagged-union payload.  ``winner`` carries the
            selected agent ID and reasoning; ``reject`` carries only
            a reasoning string.  The :class:`DecisionProcessor`
            strategy on the server decides which shapes are accepted.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    decision: EscalationDecision = Field(description="Operator decision payload")


class CancelEscalationRequest(BaseModel):
    """Body for ``POST /escalations/{id}/cancel``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    reason: NotBlankStr = Field(
        max_length=4096,
        description="Why the operator is abandoning this escalation",
    )


# ── Helpers ─────────────────────────────────────────────────────


def _to_response(escalation: Escalation) -> EscalationResponse:
    """Wrap an :class:`Escalation` in the API response envelope.

    Returns:
        ``EscalationResponse`` instance.
    """
    return EscalationResponse(
        escalation=escalation,
        conflict_id=escalation.conflict.id,
        status=escalation.status,
    )


def _operator_id() -> str:
    """Resolve the deciding operator id, prefixed with ``human:``.

    RFC#3 / ADR-0003: the identity comes from the actor seam bound by
    ``AuthContextMiddleware`` (``actor_id`` == immutable user id)
    rather than re-reading ``request.scope["user"]``. The
    ``human:<user_id>`` shape is byte-identical to the previous
    derivation so persisted escalation decisions are unchanged. An
    unbound context surfaces as :class:`ActorContextMissingError`
    (500): the middleware binds before any controller runs.

    Returns:
        Resulting string.
    """
    actor = require_actor()
    return f"human:{actor.actor_id}"


# ── Controller ──────────────────────────────────────────────────


class EscalationsController(Controller):
    """``/conflicts/escalations`` endpoints."""

    path = "/conflicts/escalations"
    tags = ("conflict-escalations",)

    @get(
        guards=[
            require_read_access,
            per_op_rate_limit_from_policy("escalations.list", key="user"),
        ],
    )
    async def list_escalations(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
        status: Annotated[
            EscalationStatus,
            QueryParameter(
                description="Filter to escalations in this status (default PENDING)."
            ),
        ] = EscalationStatus.PENDING,
    ) -> PaginatedResponse[EscalationResponse]:
        """Page over escalations filtered by ``status`` (default PENDING).

        Returns:
            ``PaginatedResponse[EscalationResponse]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        store = app_state.slice(CommunicationStateSlice).escalation_store
        if store is None:
            msg = "Escalation queue is not configured"
            logger.warning(
                CONFLICT_ESCALATION_RESOLVED,
                note="escalation_store_not_configured",
            )
            raise NotFoundError(msg)
        secret = cursor_secret_of(app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        page, total = await store.list_items(
            status=status,
            limit=limit,
            offset=offset,
        )
        next_offset = offset + len(page)
        has_more = next_offset < total
        next_cursor = encode_cursor(next_offset, secret=secret) if has_more else None
        return PaginatedResponse[EscalationResponse](
            data=tuple(_to_response(row) for row in page),
            pagination=PaginationMeta(
                limit=limit,
                next_cursor=next_cursor,
                has_more=has_more,
            ),
        )

    @get(
        "/{escalation_id:str}",
        guards=[
            require_read_access,
            per_op_rate_limit_from_policy("escalations.get", key="user"),
        ],
    )
    async def get_escalation(
        self,
        state: State,
        escalation_id: PathId,
    ) -> ApiResponse[EscalationResponse]:
        """Return a single escalation by ID.

        Returns:
            ``ApiResponse[EscalationResponse]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        store = app_state.slice(CommunicationStateSlice).escalation_store
        if store is None:
            msg = "Escalation queue is not configured"
            logger.warning(
                CONFLICT_ESCALATION_RESOLVED,
                note="escalation_store_not_configured",
            )
            raise NotFoundError(msg)
        row = await store.get(escalation_id)
        if row is None:
            msg = f"Escalation {escalation_id!r} not found"
            logger.warning(
                CONFLICT_ESCALATION_RESOLVED,
                escalation_id=escalation_id,
                note="get_escalation_not_found",
            )
            raise NotFoundError(msg)
        return ApiResponse[EscalationResponse](data=_to_response(row))

    @post(
        "/{escalation_id:str}/decision",
        guards=[
            require_approval_roles,
            per_op_rate_limit_from_policy("escalations.decide", key="user"),
        ],
    )
    async def submit_decision(
        self,
        state: State,
        escalation_id: PathId,
        data: SubmitDecisionRequest,
    ) -> ApiResponse[EscalationResponse]:
        """Apply an operator decision to a PENDING escalation.

        The store transition to DECIDED is atomic.  After the row is
        persisted, the in-process :class:`PendingFuturesRegistry` is
        notified so the awaiting :class:`HumanEscalationResolver`
        coroutine wakes with the decision.

        Raises:
            NotFoundError: ``escalation_id`` does not exist.
            ConflictError: the escalation is already decided, expired,
                or cancelled.
            ValidationError: the decision shape is not accepted by
                the server's configured decision strategy.

        Returns:
            ``ApiResponse[EscalationResponse]`` instance.
        """
        app_state: AppState = state.app_state
        store = app_state.slice(CommunicationStateSlice).escalation_store
        registry = app_state.slice(CommunicationStateSlice).escalation_registry
        processor = app_state.slice(CommunicationStateSlice).escalation_processor
        if store is None or registry is None or processor is None:
            msg = "Escalation queue is not configured"
            logger.warning(
                CONFLICT_ESCALATION_RESOLVED,
                note="escalation_subsystem_not_configured",
                missing_store=store is None,
                missing_registry=registry is None,
                missing_processor=processor is None,
            )
            raise NotFoundError(msg)

        operator = _operator_id()
        row = await store.get(escalation_id)
        if row is None:
            msg = f"Escalation {escalation_id!r} not found"
            logger.warning(
                CONFLICT_ESCALATION_RESOLVED,
                escalation_id=escalation_id,
                operator=operator,
                note="submit_decision_not_found",
            )
            raise NotFoundError(msg)
        if row.status != EscalationStatus.PENDING:
            msg = (
                f"Escalation {escalation_id!r} is {row.status.value}, "
                "cannot submit a decision"
            )
            logger.warning(
                CONFLICT_ESCALATION_RESOLVED,
                escalation_id=escalation_id,
                operator=operator,
                current_status=row.status.value,
                note="submit_decision_not_pending",
            )
            raise ConflictError(msg)

        # Run the processor eagerly so an unacceptable decision shape
        # (e.g. 'reject' when decision_strategy='winner') surfaces as
        # 422 before the store transition.
        try:
            processor.process(row.conflict, data.decision, decided_by=operator)
        except EscalationDecisionError as exc:
            logger.warning(
                CONFLICT_ESCALATION_RESOLVED,
                escalation_id=escalation_id,
                operator=operator,
                decision_type=data.decision.type,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ValidationError(str(exc)) from exc

        try:
            updated = await store.apply_decision(
                escalation_id,
                decision=data.decision,
                decided_by=operator,
            )
        except KeyError as exc:
            msg = f"Escalation {escalation_id!r} not found"
            logger.warning(
                CONFLICT_ESCALATION_RESOLVED,
                escalation_id=escalation_id,
                operator=operator,
                decision_type=data.decision.type,
                error_type="apply_decision_not_found",
                error=safe_error_description(exc),
                note="race_escalation_deleted_between_get_and_apply",
            )
            raise NotFoundError(msg) from exc
        except ValueError as exc:
            logger.warning(
                CONFLICT_ESCALATION_RESOLVED,
                escalation_id=escalation_id,
                operator=operator,
                decision_type=data.decision.type,
                error_type="apply_decision_invalid_transition",
                error=safe_error_description(exc),
            )
            raise ConflictError(str(exc)) from exc
        woke_resolver = await registry.resolve(escalation_id, data.decision)
        logger.info(
            CONFLICT_ESCALATION_RESOLVED,
            escalation_id=escalation_id,
            operator=operator,
            decision_type=data.decision.type,
            resolver_woken=woke_resolver,
            note=(
                "delivered_to_resolver"
                if woke_resolver
                else "persisted_only_no_live_resolver"
            ),
        )
        record_escalation_outcome(outcome="resolved")
        return ApiResponse[EscalationResponse](data=_to_response(updated))

    @post(
        "/{escalation_id:str}/cancel",
        guards=[
            require_approval_roles,
            per_op_rate_limit_from_policy("escalations.cancel", key="user"),
        ],
    )
    async def cancel_escalation(
        self,
        state: State,
        escalation_id: PathId,
        data: CancelEscalationRequest,
    ) -> ApiResponse[EscalationResponse]:
        """Abandon a PENDING escalation.

        The awaiting resolver coroutine wakes with a ``CancelledError``
        and returns an ``ESCALATED_TO_HUMAN`` resolution marked as
        cancelled.

        Returns:
            ``ApiResponse[EscalationResponse]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
            ConflictError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        store = app_state.slice(CommunicationStateSlice).escalation_store
        registry = app_state.slice(CommunicationStateSlice).escalation_registry
        if store is None or registry is None:
            msg = "Escalation queue is not configured"
            logger.warning(
                CONFLICT_ESCALATION_CANCELLED,
                note="escalation_subsystem_not_configured",
                missing_store=store is None,
                missing_registry=registry is None,
            )
            raise NotFoundError(msg)
        operator = _operator_id()
        try:
            updated = await store.cancel(escalation_id, cancelled_by=operator)
        except KeyError as exc:
            msg = f"Escalation {escalation_id!r} not found"
            logger.warning(
                CONFLICT_ESCALATION_CANCELLED,
                escalation_id=escalation_id,
                operator=operator,
                error_type="cancel_not_found",
                error=safe_error_description(exc),
            )
            raise NotFoundError(msg) from exc
        except ValueError as exc:
            logger.warning(
                CONFLICT_ESCALATION_CANCELLED,
                escalation_id=escalation_id,
                operator=operator,
                error_type="cancel_invalid_transition",
                error=safe_error_description(exc),
            )
            raise ConflictError(str(exc)) from exc
        await registry.cancel(escalation_id)
        logger.info(
            CONFLICT_ESCALATION_CANCELLED,
            escalation_id=escalation_id,
            operator=operator,
            reason=data.reason,
        )
        return ApiResponse[EscalationResponse](data=_to_response(updated))
