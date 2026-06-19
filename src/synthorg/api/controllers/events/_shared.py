"""Shared interrupt DTOs and resume helpers for the event controllers.

The SSE ``/events`` controller and the polling ``/interrupts`` controller
both expose a resume endpoint; this module holds the request/response
DTOs, the store/auth accessors, payload validation, and the common
``_resolve_interrupt`` body they share, plus the session-id alphabet
guard used by both query parameters.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.dto import ApiResponse
from synthorg.api.state import AppState
from synthorg.communication.event_stream.interrupt import (
    INTERRUPT_FIELD_RULES,
    Interrupt,
    InterruptResolution,
    InterruptStore,
    InterruptType,
    ResumeDecision,
)
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.domain_errors import (
    NotFoundError,
    ValidationError,
)
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_REQUEST_ERROR, API_VALIDATION_FAILED
from synthorg.observability.events.event_stream import EVENT_STREAM_INTERRUPT_NOT_FOUND

logger = get_logger(__name__)

# Session IDs flow into a hub keyed on the value -- restrict the alphabet
# to alphanumerics + dash + underscore to block path-traversal-shaped or
# control-character session IDs reaching the hub.
_SESSION_ID_PATTERN = r"^[a-zA-Z0-9_-]{1,128}$"


class ResumeInterruptRequest(BaseModel):
    """Request body for resuming an interrupt."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    decision: ResumeDecision | None = Field(
        default=None,
        description="Approval decision (TOOL_APPROVAL only)",
    )
    feedback: NotBlankStr | None = Field(
        default=None,
        description="Feedback text (TOOL_APPROVAL only)",
    )
    response: NotBlankStr | None = Field(
        default=None,
        description="Clarification response (INFO_REQUEST only)",
    )


class InterruptResponse(BaseModel):
    """Interrupt item returned by the polling API."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    type: InterruptType
    session_id: NotBlankStr
    agent_id: NotBlankStr
    created_at: str
    timeout_seconds: float
    tool_name: NotBlankStr | None = None
    tool_args: dict[str, object] | None = None
    evidence_package_id: NotBlankStr | None = None
    question: NotBlankStr | None = None
    context_snippet: NotBlankStr | None = None


def _require_interrupt_store(app_state: AppState) -> InterruptStore:
    """Return the interrupt store or raise when unavailable.

    Raises:
        NotFoundError: Raised on the corresponding failure path.
    """
    store = app_state.slice(CommunicationStateSlice).interrupt_store
    if store is None:
        logger.warning(API_REQUEST_ERROR, reason="interrupt_store_not_configured")
        msg = "Interrupt store not configured"
        raise NotFoundError(msg)
    return store


def _validate_resume_payload(
    interrupt: Interrupt,
    data: ResumeInterruptRequest,
) -> None:
    """Validate resume payload matches the interrupt type.

    Args:
        interrupt: The pending interrupt being resumed.
        data: The client's resume payload.

    Raises:
        ValidationError: If required fields are missing.
    """
    rule = INTERRUPT_FIELD_RULES.get(interrupt.type)
    if rule is not None and getattr(data, rule.resume_field) is None:
        msg = f"{interrupt.type.name} interrupts require a {rule.resume_field}"
        logger.warning(
            API_VALIDATION_FAILED,
            reason="resume_payload_missing_field",
            interrupt_type=interrupt.type.value,
            missing_field=rule.resume_field,
        )
        raise ValidationError(msg)


async def _resolve_interrupt(
    store: InterruptStore,
    interrupt_id: str,
    data: ResumeInterruptRequest,
    resolved_by: str,
) -> ApiResponse[dict[str, str]]:
    """Shared logic for both resume endpoints.

    Args:
        store: The interrupt store.
        interrupt_id: The interrupt to resume.
        data: The resume payload.
        resolved_by: Identity of the resolver.

    Returns:
        Confirmation envelope.

    Raises:
        NotFoundError: If interrupt doesn't exist or is no longer pending.
        ValidationError: If payload doesn't match interrupt type.
    """
    interrupt = await store.get(interrupt_id)
    if interrupt is None:
        logger.warning(
            EVENT_STREAM_INTERRUPT_NOT_FOUND,
            interrupt_id=interrupt_id,
        )
        msg = f"Interrupt {interrupt_id!r} not found"
        raise NotFoundError(msg)

    _validate_resume_payload(interrupt, data)

    resolution = InterruptResolution(
        interrupt_id=interrupt_id,
        decision=data.decision,
        feedback=data.feedback,
        response=data.response,
        resolved_at=datetime.now(UTC),
        resolved_by=resolved_by,
    )
    resolved = await store.resolve(resolution)
    if resolved is None:
        logger.warning(
            EVENT_STREAM_INTERRUPT_NOT_FOUND,
            interrupt_id=interrupt_id,
            reason="interrupt_no_longer_pending",
        )
        msg = f"Interrupt {interrupt_id!r} is no longer pending"
        raise NotFoundError(msg)

    return ApiResponse(data={"status": "resumed"})
