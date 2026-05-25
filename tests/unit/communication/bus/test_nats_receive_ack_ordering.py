# mypy: disable-error-code="explicit-any"
"""Regression coverage: NATS receive defers JetStream ack until delivery.

The previous implementation called ``msg.ack()`` from inside
``build_envelope`` before returning the envelope; if the subscriber's
delivery path then raised, the JetStream message was already
acknowledged and could never be redelivered. ``DeliveryEnvelope.ack``
now exposes a deferred callable that callers MUST invoke after their
local consumer accepts the envelope.

These tests pin two invariants:

1. ``build_envelope`` MUST NOT call ``msg.ack()`` synchronously on the
   happy path.
2. The returned envelope's ``ack()`` callable forwards to the JetStream
   ``msg.ack()`` so callers retain a path to acknowledge after delivery.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from synthorg.communication.bus._nats_receive import build_envelope
from synthorg.communication.enums import MessageType
from synthorg.communication.message import Message, TextPart
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit


class _FakeNatsMessage:
    """Minimal stand-in for an ``nats-py`` JetStream message."""

    def __init__(self, payload: bytes) -> None:
        self.data = payload
        self.ack_count = 0

    async def ack(self) -> None:
        self.ack_count += 1


def _envelope_bytes() -> bytes:
    msg = Message(
        timestamp=datetime.now(UTC),
        sender=NotBlankStr("agent-a"),
        to=NotBlankStr("agent-b"),
        type=MessageType.ANNOUNCEMENT,
        channel=NotBlankStr("#general"),
        parts=(TextPart(text="hello"),),
    )
    return msg.model_dump_json(by_alias=True).encode("utf-8")


class TestNatsReceiveAckOrdering:
    async def test_build_envelope_does_not_ack_on_success_path(self) -> None:
        msg = _FakeNatsMessage(_envelope_bytes())
        envelope = await build_envelope(
            [msg],
            channel_name="#general",
            subscriber_id="agent-b",
        )
        assert envelope is not None
        assert msg.ack_count == 0, (
            "build_envelope must NOT ack before the subscriber accepts delivery"
        )

    async def test_envelope_ack_forwards_to_msg_ack(self) -> None:
        msg = _FakeNatsMessage(_envelope_bytes())
        envelope = await build_envelope(
            [msg],
            channel_name="#general",
            subscriber_id="agent-b",
        )
        assert envelope is not None
        await envelope.ack()
        assert msg.ack_count == 1


class TestNatsReceiveOversizedAckImmediately:
    async def test_oversized_payload_is_acked_immediately(self) -> None:
        oversized = b"\x00" * (10 * 1024 * 1024)
        msg = _FakeNatsMessage(oversized)
        envelope = await build_envelope(
            [msg],
            channel_name="#general",
            subscriber_id="agent-b",
        )
        # No envelope to deliver, so we MUST ack synchronously to drop
        # the malformed message from JetStream.
        assert envelope is None
        assert msg.ack_count == 1


# Force pytest-asyncio to pick up the coroutine fixtures above.
__all__: tuple[Any, ...] = ()
