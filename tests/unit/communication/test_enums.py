"""Tests for the communication domain enumerations."""

import pytest

from ai_company.communication.enums import (
    AttachmentType,
    ChannelType,
    CommunicationPattern,
    MessageBusBackend,
    MessagePriority,
    MessageType,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestMessageType:
    def test_member_count(self) -> None:
        assert len(MessageType) == 9

    def test_values(self) -> None:
        assert MessageType.TASK_UPDATE.value == "task_update"
        assert MessageType.QUESTION.value == "question"
        assert MessageType.ANNOUNCEMENT.value == "announcement"
        assert MessageType.REVIEW_REQUEST.value == "review_request"
        assert MessageType.APPROVAL.value == "approval"
        assert MessageType.DELEGATION.value == "delegation"
        assert MessageType.STATUS_REPORT.value == "status_report"
        assert MessageType.ESCALATION.value == "escalation"
        assert MessageType.MEETING_CONTRIBUTION.value == "meeting_contribution"

    def test_string_identity(self) -> None:
        assert str(MessageType.TASK_UPDATE) == "task_update"


@pytest.mark.unit
class TestMessagePriority:
    def test_member_count(self) -> None:
        assert len(MessagePriority) == 4

    def test_values(self) -> None:
        assert MessagePriority.LOW.value == "low"
        assert MessagePriority.NORMAL.value == "normal"
        assert MessagePriority.HIGH.value == "high"
        assert MessagePriority.URGENT.value == "urgent"

    def test_normal_not_medium(self) -> None:
        """Message priority uses 'normal', not 'medium' like task Priority."""
        member_values = {m.value for m in MessagePriority}
        assert "normal" in member_values
        assert "medium" not in member_values


@pytest.mark.unit
class TestChannelType:
    def test_member_count(self) -> None:
        assert len(ChannelType) == 3

    def test_values(self) -> None:
        assert ChannelType.TOPIC.value == "topic"
        assert ChannelType.DIRECT.value == "direct"
        assert ChannelType.BROADCAST.value == "broadcast"


@pytest.mark.unit
class TestAttachmentType:
    def test_member_count(self) -> None:
        assert len(AttachmentType) == 3

    def test_values(self) -> None:
        assert AttachmentType.ARTIFACT.value == "artifact"
        assert AttachmentType.FILE.value == "file"
        assert AttachmentType.LINK.value == "link"


@pytest.mark.unit
class TestCommunicationPattern:
    def test_member_count(self) -> None:
        assert len(CommunicationPattern) == 4

    def test_values(self) -> None:
        assert CommunicationPattern.EVENT_DRIVEN.value == "event_driven"
        assert CommunicationPattern.HIERARCHICAL.value == "hierarchical"
        assert CommunicationPattern.MEETING_BASED.value == "meeting_based"
        assert CommunicationPattern.HYBRID.value == "hybrid"


@pytest.mark.unit
class TestCommunicationExports:
    def test_all_exports_importable(self) -> None:
        import ai_company.communication as comm_module

        for name in comm_module.__all__:
            assert hasattr(comm_module, name), f"{name} in __all__ but not importable"


@pytest.mark.unit
class TestMessageBusBackend:
    def test_member_count(self) -> None:
        assert len(MessageBusBackend) == 4

    def test_values(self) -> None:
        assert MessageBusBackend.INTERNAL.value == "internal"
        assert MessageBusBackend.REDIS.value == "redis"
        assert MessageBusBackend.RABBITMQ.value == "rabbitmq"
        assert MessageBusBackend.KAFKA.value == "kafka"
