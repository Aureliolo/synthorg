"""WebSocket event models for real-time feeds.

Defines event types and the ``WsEvent`` payload that is
serialised to JSON and pushed to WebSocket subscribers.
"""

from enum import StrEnum
from typing import Any

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
)


class WsEventType(StrEnum):
    """Types of real-time WebSocket events."""

    TASK_CREATED = "task.created"
    TASK_UPDATED = "task.updated"
    TASK_STATUS_CHANGED = "task.status_changed"
    TASK_ASSIGNED = "task.assigned"

    AGENT_HIRED = "agent.hired"
    AGENT_FIRED = "agent.fired"
    AGENT_STATUS_CHANGED = "agent.status_changed"

    BUDGET_RECORD_ADDED = "budget.record_added"
    BUDGET_ALERT = "budget.alert"

    MESSAGE_SENT = "message.sent"

    SYSTEM_ERROR = "system.error"
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"


class WsEvent(BaseModel):
    """A real-time event pushed over WebSocket.

    Attributes:
        event_type: Classification of the event.
        channel: Target channel name.
        timestamp: When the event occurred.
        payload: Event-specific data.
    """

    model_config = ConfigDict(frozen=True)

    event_type: WsEventType = Field(
        description="Event classification",
    )
    channel: str = Field(min_length=1, description="Target channel name")
    timestamp: AwareDatetime = Field(
        description="When the event occurred",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Event-specific data",
    )
