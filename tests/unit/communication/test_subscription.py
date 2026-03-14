"""Unit tests for Subscription and DeliveryEnvelope models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.communication.enums import (
    MessagePriority,
    MessageType,
)
from synthorg.communication.message import Message
from synthorg.communication.subscription import DeliveryEnvelope, Subscription

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


class TestSubscription:
    """Tests for the Subscription model."""

    def test_create_subscription(self) -> None:
        sub = Subscription(
            channel_name="#engineering",
            subscriber_id="agent-1",
            subscribed_at=datetime(2026, 3, 7, tzinfo=UTC),
        )
        assert sub.channel_name == "#engineering"
        assert sub.subscriber_id == "agent-1"
        assert sub.subscribed_at.tzinfo is not None

    def test_frozen(self) -> None:
        sub = Subscription(
            channel_name="#eng",
            subscriber_id="a1",
            subscribed_at=datetime(2026, 3, 7, tzinfo=UTC),
        )
        with pytest.raises(ValidationError):
            sub.channel_name = "new"  # type: ignore[misc]

    def test_blank_channel_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Subscription(
                channel_name="  ",
                subscriber_id="a1",
                subscribed_at=datetime(2026, 3, 7, tzinfo=UTC),
            )

    def test_blank_subscriber_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Subscription(
                channel_name="#eng",
                subscriber_id="  ",
                subscribed_at=datetime(2026, 3, 7, tzinfo=UTC),
            )


class TestDeliveryEnvelope:
    """Tests for the DeliveryEnvelope model."""

    def test_create_envelope(self) -> None:
        msg = Message(
            timestamp=datetime(2026, 3, 7, 10, 0, tzinfo=UTC),
            sender="alice",
            to="bob",
            type=MessageType.TASK_UPDATE,
            priority=MessagePriority.NORMAL,
            channel="#eng",
            content="hello",
        )
        envelope = DeliveryEnvelope(
            message=msg,
            channel_name="#eng",
            delivered_at=datetime(2026, 3, 7, 10, 1, tzinfo=UTC),
        )
        assert envelope.message.sender == "alice"
        assert envelope.channel_name == "#eng"

    def test_frozen(self) -> None:
        msg = Message(
            timestamp=datetime(2026, 3, 7, 10, 0, tzinfo=UTC),
            sender="alice",
            to="bob",
            type=MessageType.TASK_UPDATE,
            channel="#eng",
            content="hello",
        )
        envelope = DeliveryEnvelope(
            message=msg,
            channel_name="#eng",
            delivered_at=datetime(2026, 3, 7, 10, 1, tzinfo=UTC),
        )
        with pytest.raises(ValidationError):
            envelope.channel_name = "new"  # type: ignore[misc]

    def test_blank_channel_rejected(self) -> None:
        msg = Message(
            timestamp=datetime(2026, 3, 7, 10, 0, tzinfo=UTC),
            sender="alice",
            to="bob",
            type=MessageType.TASK_UPDATE,
            channel="#eng",
            content="hello",
        )
        with pytest.raises(ValidationError):
            DeliveryEnvelope(
                message=msg,
                channel_name="  ",
                delivered_at=datetime(2026, 3, 7, 10, 1, tzinfo=UTC),
            )
