"""Communication subsystem for the AI company framework."""

from ai_company.communication.bus_memory import InMemoryMessageBus
from ai_company.communication.bus_protocol import MessageBus
from ai_company.communication.channel import Channel
from ai_company.communication.config import (
    CircuitBreakerConfig,
    CommunicationConfig,
    HierarchyConfig,
    LoopPreventionConfig,
    MeetingsConfig,
    MeetingTypeConfig,
    MessageBusConfig,
    MessageRetentionConfig,
    RateLimitConfig,
)
from ai_company.communication.dispatcher import DispatchResult, MessageDispatcher
from ai_company.communication.enums import (
    AttachmentType,
    ChannelType,
    CommunicationPattern,
    MessageBusBackend,
    MessagePriority,
    MessageType,
)
from ai_company.communication.errors import (
    ChannelAlreadyExistsError,
    ChannelNotFoundError,
    CommunicationError,
    MessageBusAlreadyRunningError,
    MessageBusNotRunningError,
    NotSubscribedError,
)
from ai_company.communication.handler import (
    FunctionHandler,
    HandlerRegistration,
    MessageHandler,
    MessageHandlerFunc,
)
from ai_company.communication.message import Attachment, Message, MessageMetadata
from ai_company.communication.messenger import AgentMessenger
from ai_company.communication.subscription import DeliveryEnvelope, Subscription

__all__ = [
    "AgentMessenger",
    "Attachment",
    "AttachmentType",
    "Channel",
    "ChannelAlreadyExistsError",
    "ChannelNotFoundError",
    "ChannelType",
    "CircuitBreakerConfig",
    "CommunicationConfig",
    "CommunicationError",
    "CommunicationPattern",
    "DeliveryEnvelope",
    "DispatchResult",
    "FunctionHandler",
    "HandlerRegistration",
    "HierarchyConfig",
    "InMemoryMessageBus",
    "LoopPreventionConfig",
    "MeetingTypeConfig",
    "MeetingsConfig",
    "Message",
    "MessageBus",
    "MessageBusAlreadyRunningError",
    "MessageBusBackend",
    "MessageBusConfig",
    "MessageBusNotRunningError",
    "MessageDispatcher",
    "MessageHandler",
    "MessageHandlerFunc",
    "MessageMetadata",
    "MessagePriority",
    "MessageRetentionConfig",
    "MessageType",
    "NotSubscribedError",
    "RateLimitConfig",
    "Subscription",
]
