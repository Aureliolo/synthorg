"""Communication feature state slice.

Holds the messaging / meeting / event-stream / escalation services:
the message bus + message service, meeting orchestrator + scheduler +
service, the event-stream hub and interrupt store, the delegation
record store, and the conflict-resolution escalation stack (store,
registry, processor, sweeper, notify subscriber). The bus, hub,
interrupt store, and delegation store are constructor-injected; the
rest are wired lazily. All fields are ``None`` until wired; readers
guard accordingly.
"""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.communication.bus_protocol import MessageBus  # noqa: TC001
from synthorg.communication.conflict_resolution.escalation.notify import (
    EscalationNotifySubscriber,  # noqa: TC001
)
from synthorg.communication.conflict_resolution.escalation.protocol import (
    DecisionProcessor,  # noqa: TC001
    EscalationQueueStore,  # noqa: TC001
)
from synthorg.communication.conflict_resolution.escalation.registry import (
    PendingFuturesRegistry,  # noqa: TC001
)
from synthorg.communication.conflict_resolution.escalation.sweeper import (
    EscalationExpirationSweeper,  # noqa: TC001
)
from synthorg.communication.delegation.record_store import (
    DelegationRecordStore,  # noqa: TC001
)
from synthorg.communication.event_stream.interrupt import InterruptStore  # noqa: TC001
from synthorg.communication.event_stream.stream import EventStreamHub  # noqa: TC001
from synthorg.communication.meeting.orchestrator import (
    MeetingOrchestrator,  # noqa: TC001
)
from synthorg.communication.meeting.scheduler import MeetingScheduler  # noqa: TC001
from synthorg.communication.meetings.service import MeetingService  # noqa: TC001
from synthorg.communication.messages.service import MessageService  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin


class CommunicationStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the communication feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message_bus: MessageBus | None = None
    message_service: MessageService | None = None
    meeting_orchestrator: MeetingOrchestrator | None = None
    meeting_scheduler: MeetingScheduler | None = None
    meeting_service: MeetingService | None = None
    event_stream_hub: EventStreamHub | None = None
    interrupt_store: InterruptStore | None = None
    delegation_record_store: DelegationRecordStore | None = None
    escalation_store: EscalationQueueStore | None = None
    escalation_registry: PendingFuturesRegistry | None = None
    escalation_processor: DecisionProcessor | None = None
    escalation_sweeper: EscalationExpirationSweeper | None = None
    escalation_notify_subscriber: EscalationNotifySubscriber | None = None


def message_bus_of(app_state: AppStateSliceMixin) -> MessageBus:
    """Resolve the message bus from its slice, or raise 503.

    Returns:
        The wired message bus.
    """
    return require_service(
        app_state.slice(CommunicationStateSlice).message_bus, "Message Bus"
    )


def meeting_service_of(app_state: AppStateSliceMixin) -> MeetingService:
    """Resolve the meeting service from its slice, or raise 503.

    Returns:
        The wired meeting service.
    """
    return require_service(
        app_state.slice(CommunicationStateSlice).meeting_service, "Meeting Service"
    )


def message_service_of(app_state: AppStateSliceMixin) -> MessageService:
    """Resolve the message service from its slice, or raise 503.

    Returns:
        The wired message service.
    """
    return require_service(
        app_state.slice(CommunicationStateSlice).message_service, "Message Service"
    )
