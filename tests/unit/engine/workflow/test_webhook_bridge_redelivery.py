"""``WebhookEventBridge`` redelivery idempotency.

A bus message whose forward succeeded but whose ack failed is
redelivered with the same ``Message.id``. The bridge must not
re-forward it, otherwise the strategy's per-event count double-counts a
single logical delivery.
"""

from datetime import UTC, datetime
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.enums import MessageType
from synthorg.communication.message import DataPart, Message
from synthorg.engine.workflow.ceremony_scheduler import CeremonyScheduler
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.strategies.external_trigger import (
    ExternalTriggerStrategy,
)
from synthorg.engine.workflow.webhook_bridge import WebhookEventBridge
from synthorg.integrations.webhooks.event_bus_bridge import WEBHOOK_CHANNEL

pytestmark = pytest.mark.unit


def _active_sprint() -> Sprint:
    return Sprint(
        id="sprint-1",
        name="Sprint 1",
        sprint_number=1,
        status=SprintStatus.ACTIVE,
        start_date="2026-04-01",
    )


def _webhook_message() -> Message:
    data: dict[str, object] = {
        "connection_name": "gh",
        "event_type": "push",
        "payload": {},
    }
    return Message(
        timestamp=datetime(2026, 5, 13, tzinfo=UTC),
        sender="integrations:webhook-receiver",
        to=WEBHOOK_CHANNEL.name,
        type=MessageType.ANNOUNCEMENT,
        channel=WEBHOOK_CHANNEL.name,
        parts=(DataPart(data=MappingProxyType(data)),),
    )


async def test_redelivered_message_is_not_double_counted() -> None:
    strategy = ExternalTriggerStrategy()
    scheduler = MagicMock(spec=CeremonyScheduler)
    scheduler.get_active_info = AsyncMock(return_value=(strategy, _active_sprint()))
    bridge = WebhookEventBridge(MagicMock(spec=MessageBus), scheduler)

    message = _webhook_message()
    await bridge._forward(message)
    # Same Message.id: a forward-succeeds-then-ack-fails redelivery.
    await bridge._forward(message)

    assert strategy._event_counts == {"push": 1}


async def test_distinct_messages_each_count() -> None:
    strategy = ExternalTriggerStrategy()
    scheduler = MagicMock(spec=CeremonyScheduler)
    scheduler.get_active_info = AsyncMock(return_value=(strategy, _active_sprint()))
    bridge = WebhookEventBridge(MagicMock(spec=MessageBus), scheduler)

    await bridge._forward(_webhook_message())
    await bridge._forward(_webhook_message())

    assert strategy._event_counts == {"push": 2}
