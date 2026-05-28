"""Webhook event bus bridge.

Publishes verified webhook events onto the SynthOrg message bus
so that ``ExternalTriggerStrategy`` and other consumers can react.
"""

import copy
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.channel import Channel
from synthorg.communication.enums import ChannelType, MessageType
from synthorg.communication.message import DataPart, Message
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.integrations import (
    WEBHOOK_EVENT_PUBLISH_FAILED,
    WEBHOOK_EVENT_PUBLISHED,
)

logger = get_logger(__name__)

WEBHOOK_CHANNEL = Channel(name="#webhooks", type=ChannelType.TOPIC)


async def publish_webhook_event(
    *,
    bus: MessageBus,
    connection_name: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Publish a verified webhook event to the message bus.

    A verified webhook must not be silently dropped on publish
    failure: if the bus rejects the message, the exception is
    logged and re-raised so the caller returns a 5xx and the
    sender can retry.

    Args:
        bus: The message bus instance.
        connection_name: Source connection name.
        event_type: Provider-specific event type.
        payload: Webhook payload dict -- deep-copied before being
            frozen via ``MappingProxyType`` so downstream consumers
            cannot mutate the original.
    """
    message = Message(
        timestamp=datetime.now(UTC),
        sender="integrations:webhook-receiver",
        to=WEBHOOK_CHANNEL.name,
        type=MessageType.ANNOUNCEMENT,
        channel=WEBHOOK_CHANNEL.name,
        parts=(
            DataPart(
                data=MappingProxyType(
                    {
                        "connection_name": connection_name,
                        "event_type": event_type,
                        "payload": copy.deepcopy(payload),
                        "received_at": datetime.now(UTC).isoformat(),
                    }
                ),
            ),
        ),
    )
    try:
        await bus.publish(message)
    except Exception as exc:
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            WEBHOOK_EVENT_PUBLISH_FAILED,
            exc,
            connection_name=connection_name,
            event_type=event_type,
        )
        raise
    logger.info(
        WEBHOOK_EVENT_PUBLISHED,
        connection_name=connection_name,
        event_type=event_type,
    )
