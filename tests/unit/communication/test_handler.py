"""Tests for message handler protocol, adapter, and registration."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from synthorg.communication.enums import MessagePriority, MessageType
from synthorg.communication.handler import (
    FunctionHandler,
    HandlerRegistration,
    MessageHandler,
    priority_at_least,
)
from synthorg.communication.message import Message

pytestmark = pytest.mark.timeout(30)

_MESSAGE_KWARGS: dict[str, object] = {
    "timestamp": datetime(2026, 3, 7, 12, 0, tzinfo=UTC),
    "sender": "agent-a",
    "to": "agent-b",
    "type": MessageType.TASK_UPDATE,
    "channel": "#test",
    "content": "test content",
}


def _make_message(**overrides: object) -> Message:
    kwargs = {**_MESSAGE_KWARGS, **overrides}
    return Message(**kwargs)  # type: ignore[arg-type]


# ── MessageHandler protocol ───────────────────────────────────


@pytest.mark.unit
class TestMessageHandlerProtocol:
    def test_runtime_checkable(self) -> None:
        """MessageHandler protocol is runtime_checkable."""

        class _Good:
            async def handle(self, message: Message) -> None:
                pass

        assert isinstance(_Good(), MessageHandler)

    def test_non_conforming_rejected(self) -> None:
        """Object without handle() is not a MessageHandler."""

        class _Bad:
            pass

        assert not isinstance(_Bad(), MessageHandler)


# ── FunctionHandler ───────────────────────────────────────────


@pytest.mark.unit
class TestFunctionHandler:
    async def test_wraps_and_calls_async_function(self) -> None:
        """FunctionHandler delegates to the wrapped async function."""
        called_with: list[Message] = []

        async def _fn(msg: Message) -> None:
            called_with.append(msg)

        handler = FunctionHandler(_fn)
        msg = _make_message()
        await handler.handle(msg)

        assert len(called_with) == 1
        assert called_with[0] is msg

    async def test_is_message_handler(self) -> None:
        """FunctionHandler satisfies the MessageHandler protocol."""

        async def _noop(msg: Message) -> None:
            pass

        assert isinstance(FunctionHandler(_noop), MessageHandler)

    def test_rejects_non_callable(self) -> None:
        """FunctionHandler raises TypeError for non-callable input."""
        with pytest.raises(TypeError, match="must be async"):
            FunctionHandler("not a function")  # type: ignore[arg-type]

    def test_rejects_sync_function(self) -> None:
        """FunctionHandler raises TypeError for synchronous functions."""

        def _sync(msg: Message) -> None:
            pass

        with pytest.raises(TypeError, match="must be async"):
            FunctionHandler(_sync)  # type: ignore[arg-type]


# ── HandlerRegistration ──────────────────────────────────────


@pytest.mark.unit
class TestHandlerRegistration:
    def test_defaults(self) -> None:
        """HandlerRegistration has sensible defaults."""

        async def _noop(msg: Message) -> None:
            pass

        reg = HandlerRegistration(handler=FunctionHandler(_noop))
        assert isinstance(UUID(reg.handler_id), UUID)
        assert reg.message_types == frozenset()
        assert reg.min_priority is MessagePriority.LOW
        assert reg.name == "unnamed"

    def test_custom_values(self) -> None:
        """HandlerRegistration accepts custom configuration."""

        async def _noop(msg: Message) -> None:
            pass

        types = frozenset({MessageType.QUESTION, MessageType.ESCALATION})
        reg = HandlerRegistration(
            handler=FunctionHandler(_noop),
            message_types=types,
            min_priority=MessagePriority.HIGH,
            name="my-handler",
        )
        assert reg.message_types == types
        assert reg.min_priority is MessagePriority.HIGH
        assert reg.name == "my-handler"

    def test_frozen(self) -> None:
        """HandlerRegistration is immutable."""

        async def _noop(msg: Message) -> None:
            pass

        reg = HandlerRegistration(handler=FunctionHandler(_noop))
        with pytest.raises(ValidationError):
            reg.name = "changed"  # type: ignore[misc]


# ── priority_at_least ─────────────────────────────────────────


@pytest.mark.unit
class TestPriorityAtLeast:
    @pytest.mark.parametrize(
        ("value", "minimum", "expected"),
        [
            (MessagePriority.LOW, MessagePriority.LOW, True),
            (MessagePriority.NORMAL, MessagePriority.LOW, True),
            (MessagePriority.HIGH, MessagePriority.LOW, True),
            (MessagePriority.URGENT, MessagePriority.LOW, True),
            (MessagePriority.LOW, MessagePriority.NORMAL, False),
            (MessagePriority.NORMAL, MessagePriority.NORMAL, True),
            (MessagePriority.HIGH, MessagePriority.NORMAL, True),
            (MessagePriority.LOW, MessagePriority.HIGH, False),
            (MessagePriority.NORMAL, MessagePriority.HIGH, False),
            (MessagePriority.HIGH, MessagePriority.HIGH, True),
            (MessagePriority.URGENT, MessagePriority.HIGH, True),
            (MessagePriority.LOW, MessagePriority.URGENT, False),
            (MessagePriority.HIGH, MessagePriority.URGENT, False),
            (MessagePriority.URGENT, MessagePriority.URGENT, True),
        ],
    )
    def test_ordering(
        self,
        value: MessagePriority,
        minimum: MessagePriority,
        expected: bool,
    ) -> None:
        assert priority_at_least(value, minimum) is expected
