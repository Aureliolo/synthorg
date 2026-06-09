"""One-way event projector: internal observability -> AG-UI types.

Maps internal ``observability/events/`` constants to AG-UI
``StreamEvent`` objects.  The projection is one-way: internal
event constants remain the canonical source.  AG-UI is the
external-facing projection only.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from uuid import uuid4

from synthorg.communication.event_stream.types import (
    AgUiEventType,
    StreamEvent,
)
from synthorg.observability import get_logger
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_CONTEXT_PARKED,
    APPROVAL_GATE_CONTEXT_RESUMED,
)
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_COMPLETE,
    EXECUTION_ENGINE_ERROR,
    EXECUTION_ENGINE_START,
    EXECUTION_LOOP_TOOL_CALLS,
    EXECUTION_LOOP_TURN_COMPLETE,
    EXECUTION_LOOP_TURN_START,
    EXECUTION_PLAN_STEP_COMPLETE,
    EXECUTION_PLAN_STEP_FAILED,
    EXECUTION_PLAN_STEP_START,
)

logger = get_logger(__name__)

PROJECTION_MAP: Mapping[str, AgUiEventType] = MappingProxyType(
    {
        # Run lifecycle
        EXECUTION_ENGINE_START: AgUiEventType.RUN_STARTED,
        EXECUTION_ENGINE_COMPLETE: AgUiEventType.RUN_FINISHED,
        EXECUTION_ENGINE_ERROR: AgUiEventType.RUN_ERROR,
        # Plan steps
        EXECUTION_PLAN_STEP_START: AgUiEventType.STEP_STARTED,
        EXECUTION_PLAN_STEP_COMPLETE: AgUiEventType.STEP_FINISHED,
        EXECUTION_PLAN_STEP_FAILED: AgUiEventType.STEP_FAILED,
        # Model response turns
        EXECUTION_LOOP_TURN_START: AgUiEventType.TEXT_MESSAGE_START,
        EXECUTION_LOOP_TURN_COMPLETE: AgUiEventType.TEXT_MESSAGE_END,
        # Tool invocations
        EXECUTION_LOOP_TOOL_CALLS: AgUiEventType.TOOL_CALL_START,
        # Approval gate
        APPROVAL_GATE_CONTEXT_PARKED: AgUiEventType.APPROVAL_INTERRUPT,
        APPROVAL_GATE_CONTEXT_RESUMED: AgUiEventType.APPROVAL_RESUMED,
        # Dissent: emitted directly by ConflictResolutionService via
        # EventStreamHub.publish_raw() (not via projection) because
        # it carries a structured DissentPayload.
    }
)
"""Mapping from internal observability event constants to AG-UI types.

Events not in this map are not projected to the SSE stream.
TEXT_MESSAGE_CONTENT, TOOL_CALL_ARGS, TOOL_CALL_END,
INFO_REQUEST_INTERRUPT, INFO_REQUEST_RESUMED, and DISSENT are
emitted directly by their respective services (not via
observability log projection) because they carry structured
payloads that don't originate from a single log call.
"""


def project_event(
    internal_event: str,
    *,
    session_id: str,
    agent_id: str | None = None,
    correlation_id: str | None = None,
    payload: dict[str, object] | None = None,
) -> StreamEvent | None:
    """Project an internal event to an AG-UI StreamEvent.

    Args:
        internal_event: Internal observability event constant value.
        session_id: Session the event belongs to.
        agent_id: Agent that produced the event.
        correlation_id: Correlation identifier for tracing.
        payload: Event-specific data.

    Returns:
        A ``StreamEvent`` if the internal event is mapped, else ``None``.
    """
    ag_ui_type = PROJECTION_MAP.get(internal_event)
    if ag_ui_type is None:
        return None
    return StreamEvent(
        id=f"evt-{uuid4().hex}",
        type=ag_ui_type,
        timestamp=datetime.now(UTC),
        session_id=session_id,
        correlation_id=correlation_id,
        agent_id=agent_id,
        payload=payload or {},
    )
