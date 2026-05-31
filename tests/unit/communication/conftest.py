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
    ChannelType,
    MessageBusBackend,
    MessagePriority,
    MessageType,
)
from synthorg.communication.meeting.config import MeetingTypeConfig
from synthorg.communication.meeting.frequency import MeetingFrequency
from synthorg.communication.message import Message, MessageMetadata, TextPart
from synthorg.communication.subscription import DeliveryEnvelope, Subscription

# ── Factories ──────────────────────────────────────────────────────


class MessageMetadataFactory(ModelFactory[MessageMetadata]):
    __model__ = MessageMetadata
    task_id = None
    project_id = None
    tokens_used = None
    cost = None
    extra = ()


class MessageFactory(ModelFactory[Message]):
    __model__ = Message
    priority = MessagePriority.NORMAL
    metadata = MessageMetadataFactory

    @classmethod
    def parts(cls) -> tuple[TextPart, ...]:
        """Generate at least one TextPart for the message."""
        return (TextPart(text="Sample message content"),)

    @classmethod
    def attachments(cls) -> tuple[TextPart, ...]:
        """Pin ``attachments`` to a safe fixed value.

        ``attachments: tuple[Part, ...]`` is part of the ``Part``
        discriminated union. Without this override polyfactory rolls
        the union and, when it picks ``DataPart``, instantiates its
        frozen ``data: MappingProxyType[str, Any]`` field by calling
        ``MappingProxyType()`` with no argument, raising ``TypeError``
        nondeterministically. Same rationale as the ``parts`` override
        above; the model default is empty so an explicit empty tuple
        keeps factory-built messages deterministic.
        """
        return ()


class ChannelFactory(ModelFactory[Channel]):
    __model__ = Channel
    type = ChannelType.TOPIC
    subscribers = ()


class MessageRetentionConfigFactory(ModelFactory[MessageRetentionConfig]):
    __model__ = MessageRetentionConfig


class MessageBusConfigFactory(ModelFactory[MessageBusConfig]):
    __model__ = MessageBusConfig
    retention = MessageRetentionConfigFactory
    # Fixed to INTERNAL because other backends require a backend-specific
    # config sub-block (e.g. MessageBusConfig.nats when backend=nats) that
    # polyfactory cannot synthesize automatically. Tests that exercise
    # other backends construct the config explicitly.
    backend = MessageBusBackend.INTERNAL
    nats = None


class MeetingTypeConfigFactory(ModelFactory[MeetingTypeConfig]):
    __model__ = MeetingTypeConfig
    frequency = "daily"
    trigger = None
    # min_interval_seconds requires trigger-based meetings (validator).
    min_interval_seconds = None


class MeetingsConfigFactory(ModelFactory[MeetingsConfig]):
    __model__ = MeetingsConfig
    types = ()


class HierarchyConfigFactory(ModelFactory[HierarchyConfig]):
    __model__ = HierarchyConfig


class RateLimitConfigFactory(ModelFactory[RateLimitConfig]):
    __model__ = RateLimitConfig


class CircuitBreakerConfigFactory(ModelFactory[CircuitBreakerConfig]):
    __model__ = CircuitBreakerConfig
    # Ensure max_cooldown_seconds >= cooldown_seconds (validator constraint).
    cooldown_seconds = 300
    max_cooldown_seconds = 3600


class LoopPreventionConfigFactory(ModelFactory[LoopPreventionConfig]):
    __model__ = LoopPreventionConfig
    ancestry_tracking = True
    circuit_breaker = CircuitBreakerConfigFactory


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
    # Use the pinned MessageBusConfigFactory so polyfactory doesn't
    # synthesize a random NatsConfig for the optional `nats` sub-block.
    # NatsConfig.url now enforces a scheme allow-list at config load and
    # polyfactory's random string generator picks values that fail it.
    message_bus = MessageBusConfigFactory
    meetings = MeetingsConfigFactory
    loop_prevention = LoopPreventionConfigFactory
    conflict_resolution = ConflictResolutionConfigFactory


# ── Sample Fixtures ────────────────────────────────────────────────


@pytest.fixture
def sample_metadata() -> MessageMetadata:
    return MessageMetadata(
        task_id="task-123",
        project_id="proj-456",
        tokens_used=1200,
        cost=0.018,
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
        parts=(TextPart(text="Completed API endpoint for user authentication."),),
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
