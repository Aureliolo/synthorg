"""WebSocket event models for real-time feeds.

Defines event types and the ``WsEvent`` payload that is
serialised to JSON and pushed to WebSocket subscribers.
"""

import copy
from enum import StrEnum
from typing import TYPE_CHECKING, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.core.types import NotBlankStr  # noqa: TC001

if TYPE_CHECKING:
    from pydantic import TypeAdapter

# Lazy reference resolved on first use.  Avoids a circular import
# (``ws_payloads`` imports ``WsEventType`` from this module).
_PAYLOAD_ADAPTER: TypeAdapter[object] | None = None


def _get_payload_adapter() -> TypeAdapter[object]:
    """Return the cached :class:`TypeAdapter` for ``WsEventPayload``.

    Lazy-initialised to avoid a circular import: ``ws_payloads.py``
    imports :class:`WsEventType` from this module.
    """
    global _PAYLOAD_ADAPTER  # noqa: PLW0603 -- module-level cache by design
    if _PAYLOAD_ADAPTER is None:
        from pydantic import TypeAdapter  # noqa: PLC0415 -- lazy

        from synthorg.api.ws_payloads import WsEventPayload  # noqa: PLC0415 -- lazy

        _PAYLOAD_ADAPTER = TypeAdapter(WsEventPayload)
    return _PAYLOAD_ADAPTER


#: Current WebSocket wire-protocol version. Clients on older versions
#: are expected to ignore unknown-version events (see
#: ``web/src/stores/websocket.ts`` for the matching client-side check
#: against ``WS_PROTOCOL_VERSION`` in ``web/src/utils/constants.ts``).
#: Bump only when introducing a breaking change to ``WsEvent``.
WS_PROTOCOL_VERSION: int = 1


class WsEventType(StrEnum):
    """Types of real-time WebSocket events."""

    TASK_CREATED = "task.created"
    TASK_UPDATED = "task.updated"
    TASK_STATUS_CHANGED = "task.status_changed"
    TASK_ASSIGNED = "task.assigned"

    AGENT_HIRED = "agent.hired"
    AGENT_FIRED = "agent.fired"
    AGENT_STATUS_CHANGED = "agent.status_changed"
    AGENT_CREATED = "agent.created"
    AGENT_UPDATED = "agent.updated"
    AGENT_DELETED = "agent.deleted"
    AGENTS_REORDERED = "agents.reordered"

    COMPANY_UPDATED = "company.updated"

    DEPARTMENT_CREATED = "department.created"
    DEPARTMENT_UPDATED = "department.updated"
    DEPARTMENT_DELETED = "department.deleted"
    DEPARTMENTS_REORDERED = "departments.reordered"

    PERSONALITY_TRIMMED = "personality.trimmed"

    BUDGET_RECORD_ADDED = "budget.record_added"
    BUDGET_ALERT = "budget.alert"

    MESSAGE_SENT = "message.sent"

    SYSTEM_ERROR = "system.error"
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"

    APPROVAL_SUBMITTED = "approval.submitted"
    APPROVAL_APPROVED = "approval.approved"
    APPROVAL_REJECTED = "approval.rejected"
    APPROVAL_EXPIRED = "approval.expired"

    COORDINATION_STARTED = "coordination.started"
    # Reserved for per-phase progress events (not yet published).
    COORDINATION_PHASE_COMPLETED = "coordination.phase_completed"
    COORDINATION_COMPLETED = "coordination.completed"
    COORDINATION_FAILED = "coordination.failed"

    MEETING_STARTED = "meeting.started"
    MEETING_COMPLETED = "meeting.completed"
    MEETING_FAILED = "meeting.failed"

    ARTIFACT_CREATED = "artifact.created"
    ARTIFACT_DELETED = "artifact.deleted"
    ARTIFACT_CONTENT_UPLOADED = "artifact.content_uploaded"

    PROJECT_CREATED = "project.created"
    PROJECT_DELETED = "project.deleted"
    # Reserved for future status-update endpoint (not yet published).
    PROJECT_STATUS_CHANGED = "project.status_changed"

    MEMORY_FINE_TUNE_PROGRESS = "memory.fine_tune.progress"
    MEMORY_FINE_TUNE_STAGE_CHANGED = "memory.fine_tune.stage_changed"
    MEMORY_FINE_TUNE_COMPLETED = "memory.fine_tune.completed"
    MEMORY_FINE_TUNE_FAILED = "memory.fine_tune.failed"

    CLIENT_CREATED = "client.created"
    CLIENT_UPDATED = "client.updated"
    CLIENT_DEACTIVATED = "client.deactivated"
    CLIENT_DELETED = "client.deleted"

    REQUEST_SUBMITTED = "request.submitted"
    REQUEST_SCOPED = "request.scoped"
    REQUEST_APPROVED = "request.approved"
    REQUEST_REJECTED = "request.rejected"
    REQUEST_STATUS_CHANGED = "request.status_changed"

    SIMULATION_STARTED = "simulation.started"
    SIMULATION_RUNNING = "simulation.running"
    SIMULATION_PAUSED = "simulation.paused"
    SIMULATION_CANCELLED = "simulation.cancelled"
    SIMULATION_COMPLETED = "simulation.completed"
    SIMULATION_FAILED = "simulation.failed"

    REVIEW_STAGE_COMPLETED = "review.stage_completed"
    REVIEW_STAGE_DECIDED = "review.stage_decided"
    REVIEW_PIPELINE_COMPLETED = "review.pipeline_completed"

    INTERRUPT_CREATED = "interrupt.created"
    INTERRUPT_RESUMED = "interrupt.resumed"
    DISSENT_PUBLISHED = "dissent.published"


class WsEvent(BaseModel):
    """A real-time event pushed over WebSocket.

    Callers must not mutate the ``payload`` dict after construction;
    the dict is a mutable reference inside a frozen model.

    Attributes:
        version: Wire-protocol version. Clients MUST ignore events whose
            version they do not understand. Bump only when introducing a
            breaking change to ``WsEvent`` -- coordinate with the
            ``WS_PROTOCOL_VERSION`` constant in
            ``web/src/utils/constants.ts``.
        event_type: Classification of the event.
        channel: Target channel name.
        timestamp: When the event occurred.
        payload: Event-specific data.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    version: int = Field(
        default=WS_PROTOCOL_VERSION,
        ge=1,
        description="WS wire-protocol version (clients ignore unknown)",
    )
    event_type: WsEventType = Field(
        description="Event classification",
    )
    channel: NotBlankStr = Field(description="Target channel name")
    timestamp: AwareDatetime = Field(
        description="When the event occurred",
    )
    payload: dict[str, object] = Field(
        default_factory=dict,
        description="Event-specific data",
    )

    @model_validator(mode="after")
    def _deep_copy_payload(self) -> Self:
        object.__setattr__(self, "payload", copy.deepcopy(self.payload))
        return self

    @model_validator(mode="after")
    def _validate_payload_shape(self) -> Self:
        """Validate ``payload`` against the typed :data:`WsEventPayload` union.

        Every :class:`WsEventType` has a corresponding frozen Pydantic
        model in :mod:`synthorg.api.ws_payloads`; this validator merges
        the envelope ``event_type`` over the payload dict and runs it
        through the discriminated union adapter.  Spreading the
        envelope's ``event_type`` *last* makes it authoritative, so a
        caller cannot smuggle a conflicting ``payload["event_type"]``
        and have the union pick the wrong variant.  A shape mismatch
        (missing required field, wrong type, extra field) raises
        :class:`pydantic.ValidationError` at construction so the bad
        event never reaches a subscriber.
        """
        adapter = _get_payload_adapter()
        adapter.validate_python({**self.payload, "event_type": self.event_type.value})
        return self
