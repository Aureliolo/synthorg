"""Unit test configuration and fixtures for communication models."""

from datetime import UTC, datetime

import pytest
from polyfactory.factories.pydantic_factory import ModelFactory

from synthorg.communication.channel import Channel
from synthorg.communication.config import (
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
from synthorg.communication.conflict_resolution.config import (
    ConflictResolutionConfig,
    DebateConfig,
    HybridConfig,
)
from synthorg.communication.enums import (
    AttachmentType,
    ChannelType,
    MessagePriority,
    MessageType,
)
from synthorg.communication.meeting.frequency import MeetingFrequency
from synthorg.communication.message import Attachment, Message, MessageMetadata
from synthorg.communication.subscription import DeliveryEnvelope, Subscription

# ── Factories ──────────────────────────────────────────────────────


class AttachmentFactory(ModelFactory[Attachment]):
    __model__ = Attachment


class MessageMetadataFactory(ModelFactory[MessageMetadata]):
    __model__ = MessageMetadata
    task_id = None
    project_id = None
    tokens_used = None
    cost_usd = None
    extra = ()


class MessageFactory(ModelFactory[Message]):
    __model__ = Message
    priority = MessagePriority.NORMAL
    attachments = ()
    metadata = MessageMetadataFactory


class ChannelFactory(ModelFactory[Channel]):
    __model__ = Channel
    type = ChannelType.TOPIC
    subscribers = ()


class MessageRetentionConfigFactory(ModelFactory[MessageRetentionConfig]):
    __model__ = MessageRetentionConfig


class MessageBusConfigFactory(ModelFactory[MessageBusConfig]):
    __model__ = MessageBusConfig
    retention = MessageRetentionConfigFactory


class MeetingTypeConfigFactory(ModelFactory[MeetingTypeConfig]):
    __model__ = MeetingTypeConfig
    frequency = "daily"
    trigger = None


class MeetingsConfigFactory(ModelFactory[MeetingsConfig]):
    __model__ = MeetingsConfig
    types = ()


class HierarchyConfigFactory(ModelFactory[HierarchyConfig]):
    __model__ = HierarchyConfig


class RateLimitConfigFactory(ModelFactory[RateLimitConfig]):
    __model__ = RateLimitConfig


class CircuitBreakerConfigFactory(ModelFactory[CircuitBreakerConfig]):
    __model__ = CircuitBreakerConfig


class LoopPreventionConfigFactory(ModelFactory[LoopPreventionConfig]):
    __model__ = LoopPreventionConfig
    ancestry_tracking = True


class SubscriptionFactory(ModelFactory[Subscription]):
    __model__ = Subscription


class DeliveryEnvelopeFactory(ModelFactory[DeliveryEnvelope]):
    __model__ = DeliveryEnvelope
    message = MessageFactory


class DebateConfigFactory(ModelFactory[DebateConfig]):
    __model__ = DebateConfig


class HybridConfigFactory(ModelFactory[HybridConfig]):
    __model__ = HybridConfig


class ConflictResolutionConfigFactory(ModelFactory[ConflictResolutionConfig]):
    __model__ = ConflictResolutionConfig


class CommunicationConfigFactory(ModelFactory[CommunicationConfig]):
    __model__ = CommunicationConfig
    meetings = MeetingsConfigFactory
    loop_prevention = LoopPreventionConfigFactory
    conflict_resolution = ConflictResolutionConfigFactory


# ── Sample Fixtures ────────────────────────────────────────────────


@pytest.fixture
def sample_attachment() -> Attachment:
    return Attachment(type=AttachmentType.ARTIFACT, ref="pr-42")


@pytest.fixture
def sample_metadata() -> MessageMetadata:
    return MessageMetadata(
        task_id="task-123",
        project_id="proj-456",
        tokens_used=1200,
        cost_usd=0.018,
    )


@pytest.fixture
def sample_message(sample_metadata: MessageMetadata) -> Message:
    return Message(
        timestamp=datetime(2026, 2, 27, 10, 30, tzinfo=UTC),
        sender="sarah_chen",
        to="engineering",
        type=MessageType.TASK_UPDATE,
        priority=MessagePriority.NORMAL,
        channel="#backend",
        content="Completed API endpoint for user authentication.",
        attachments=(Attachment(type=AttachmentType.ARTIFACT, ref="pr-42"),),
        metadata=sample_metadata,
    )


@pytest.fixture
def sample_channel() -> Channel:
    return Channel(
        name="#engineering",
        type=ChannelType.TOPIC,
        subscribers=("sarah_chen", "backend_lead"),
    )


@pytest.fixture
def sample_meeting_type() -> MeetingTypeConfig:
    return MeetingTypeConfig(
        name="daily_standup",
        frequency=MeetingFrequency.PER_SPRINT_DAY,
        participants=("engineering", "qa"),
        duration_tokens=2000,
    )


@pytest.fixture
def sample_communication_config() -> CommunicationConfig:
    return CommunicationConfig()


@pytest.fixture
def sample_subscription() -> Subscription:
    return Subscription(
        channel_name="#engineering",
        subscriber_id="agent-a",
        subscribed_at=datetime(2026, 3, 7, 10, 0, tzinfo=UTC),
    )


@pytest.fixture
def sample_delivery_envelope(
    sample_message: Message,
) -> DeliveryEnvelope:
    return DeliveryEnvelope(
        message=sample_message,
        channel_name="#backend",
        delivered_at=datetime(2026, 3, 7, 10, 1, tzinfo=UTC),
    )


@pytest.fixture
def sample_bus_config() -> MessageBusConfig:
    return MessageBusConfig(
        channels=("#test-channel",),
        retention=MessageRetentionConfig(max_messages_per_channel=100),
    )
