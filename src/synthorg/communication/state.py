"""Communication feature state slice.

Holds the messaging / event-stream services: the message bus + message
service, the event-stream hub and interrupt store, and the delegation
record store. The bus, hub, interrupt store, and delegation store are
constructor-injected; the rest are wired lazily. All fields are
``None`` until wired; readers guard accordingly.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.api.state_slices import AppStateSliceMixin
from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.delegation.record_store import (
    DelegationRecordStore,
)
from synthorg.communication.event_stream.interrupt import InterruptStore
from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.communication.messages.service import MessageService


class CommunicationStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the communication feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    message_bus: MessageBus | None = None
    message_service: MessageService | None = None
    event_stream_hub: EventStreamHub | None = None
    interrupt_store: InterruptStore | None = None
    delegation_record_store: DelegationRecordStore | None = None


def message_bus_of(app_state: AppStateSliceMixin) -> MessageBus:
    """Resolve the message bus from its slice, or raise 503.

    Returns:
        The wired message bus.
    """
    return require_service(
        app_state.slice(CommunicationStateSlice).message_bus, "Message Bus"
    )


def message_service_of(app_state: AppStateSliceMixin) -> MessageService:
    """Resolve the message service from its slice, or raise 503.

    Returns:
        The wired message service.
    """
    return require_service(
        app_state.slice(CommunicationStateSlice).message_service, "Message Service"
    )
