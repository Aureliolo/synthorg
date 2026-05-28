"""AG-UI event types and stream event model.

Defines the AG-UI-aligned event type enumeration and the ``StreamEvent``
model that carries projected events over SSE to dashboard clients.
"""

import copy
from enum import StrEnum
from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.core.types import NotBlankStr


class AgUiEventType(StrEnum):
    """AG-UI protocol event types.

    One-way projection targets for internal observability events.
    Internal ``observability/events/`` constants remain canonical;
    these are the external-facing names emitted over SSE.

    Members:
        RUN_STARTED: Execution engine started a run.
        RUN_FINISHED: Execution engine completed a run.
        RUN_ERROR: Execution engine encountered an error.
        STEP_STARTED: Plan step execution started.
        STEP_FINISHED: Plan step execution completed.
        STEP_FAILED: Plan step execution failed.
        TEXT_MESSAGE_START: Model response turn started.
        TEXT_MESSAGE_CONTENT: Streamed content chunk.
        TEXT_MESSAGE_END: Model response turn completed.
        TOOL_CALL_START: Tool invocation started.
        TOOL_CALL_ARGS: Tool arguments (streamed).
        TOOL_CALL_END: Tool invocation completed.
        APPROVAL_INTERRUPT: Approval gate parked execution.
        APPROVAL_RESUMED: Approval gate resumed execution.
        INFO_REQUEST_INTERRUPT: Agent requested clarification.
        INFO_REQUEST_RESUMED: Clarification provided, execution resumed.
        DISSENT: Conflict dissent recorded (SynthOrg extension).
    """

    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    RUN_ERROR = "run_error"
    STEP_STARTED = "step_started"
    STEP_FINISHED = "step_finished"
    STEP_FAILED = "step_failed"
    TEXT_MESSAGE_START = "text_message_start"
    TEXT_MESSAGE_CONTENT = "text_message_content"
    TEXT_MESSAGE_END = "text_message_end"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_ARGS = "tool_call_args"
    TOOL_CALL_END = "tool_call_end"
    APPROVAL_INTERRUPT = "approval_interrupt"
    APPROVAL_RESUMED = "approval_resumed"
    INFO_REQUEST_INTERRUPT = "info_request_interrupt"
    INFO_REQUEST_RESUMED = "info_request_resumed"
    DISSENT = "synthorg:dissent"


class StreamEvent(BaseModel):
    """A single event on the AG-UI SSE stream.

    The ``payload`` dict is deep-copied at construction to preserve
    immutability of the frozen model.

    Attributes:
        id: Unique event identifier.
        type: AG-UI event type classification.
        timestamp: When the event was produced.
        session_id: Session this event belongs to.
        correlation_id: Optional correlation identifier for tracing.
        agent_id: Agent that produced the event, if applicable.
        payload: Event-specific data (deep-copied at construction).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique event identifier")
    type: AgUiEventType = Field(description="AG-UI event type")
    timestamp: AwareDatetime = Field(description="Event timestamp")
    session_id: NotBlankStr = Field(description="Owning session")
    correlation_id: NotBlankStr | None = Field(
        default=None,
        description="Correlation identifier for tracing",
    )
    agent_id: NotBlankStr | None = Field(
        default=None,
        description="Producing agent identifier",
    )
    payload: dict[str, object] = Field(
        default_factory=dict,
        description="Event-specific data",
    )

    @model_validator(mode="after")
    def _deep_copy_payload(self) -> Self:
        """Deep-copy payload to prevent external mutation."""
        object.__setattr__(self, "payload", copy.deepcopy(self.payload))
        return self
