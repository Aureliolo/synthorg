"""Tests for the MessageDispatcher."""

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.communication.dispatcher import DispatchResult, MessageDispatcher
from synthorg.communication.enums import MessagePriority, MessageType
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


# ── Helpers ───────────────────────────────────────────────────


class _RecordingHandler:
    """Handler that records received messages."""

    def __init__(self) -> None:
        self.received: list[Message] = []

    async def handle(self, message: Message) -> None:
        self.received.append(message)


class _FailingHandler:
    """Handler that always raises."""

    def __init__(self, error_msg: str = "boom") -> None:
        self._error_msg = error_msg

    async def handle(self, message: Message) -> None:
        raise RuntimeError(self._error_msg)


# ── Register / Deregister ─────────────────────────────────────


@pytest.mark.unit
class TestRegisterDeregister:
    def test_register_returns_id(self) -> None:
        dispatcher = MessageDispatcher()
        handler = _RecordingHandler()
        handler_id = dispatcher.register(handler, name="rec")
        assert isinstance(handler_id, str)
        assert len(handler_id) > 0

    def test_deregister_returns_true_when_found(self) -> None:
        dispatcher = MessageDispatcher()
        handler_id = dispatcher.register(_RecordingHandler())
        assert dispatcher.deregister(handler_id) is True

    def test_deregister_returns_false_when_not_found(self) -> None:
        dispatcher = MessageDispatcher()
        assert dispatcher.deregister("nonexistent") is False

    def test_register_bare_function_wraps_in_function_handler(self) -> None:
        """Bare async functions are wrapped in FunctionHandler."""
        dispatcher = MessageDispatcher()

        async def _fn(msg: Message) -> None:
            pass

        handler_id = dispatcher.register(_fn, name="func")
        assert isinstance(handler_id, str)
        # Verify wrapped — dispatch should work
        assert len(handler_id) > 0

    def test_register_multiple_handlers(self) -> None:
        dispatcher = MessageDispatcher()
        ids = [dispatcher.register(_RecordingHandler()) for _ in range(5)]
        assert len(set(ids)) == 5

    @pytest.mark.unit
    def test_register_sync_handler_raises(self) -> None:
        """Registering a handler with sync handle() raises TypeError."""
        dispatcher = MessageDispatcher()

        class _SyncHandler:
            def handle(self, message: Message) -> None:
                pass

        with pytest.raises(TypeError, match="synchronous handle"):
            dispatcher.register(_SyncHandler(), name="sync")  # type: ignore[arg-type]


# ── Dispatch Routing ──────────────────────────────────────────


@pytest.mark.unit
class TestDispatchRouting:
    async def test_dispatch_to_matching_type(self) -> None:
        """Handlers matching message type receive the message."""
        dispatcher = MessageDispatcher()
        handler = _RecordingHandler()
        dispatcher.register(
            handler,
            message_types=frozenset({MessageType.TASK_UPDATE}),
        )

        msg = _make_message(type=MessageType.TASK_UPDATE)
        result = await dispatcher.dispatch(msg)

        assert result.handlers_matched == 1
        assert result.handlers_succeeded == 1
        assert len(handler.received) == 1

    async def test_no_dispatch_to_non_matching_type(self) -> None:
        """Handlers not matching message type do not receive it."""
        dispatcher = MessageDispatcher()
        handler = _RecordingHandler()
        dispatcher.register(
            handler,
            message_types=frozenset({MessageType.QUESTION}),
        )

        msg = _make_message(type=MessageType.TASK_UPDATE)
        result = await dispatcher.dispatch(msg)

        assert result.handlers_matched == 0
        assert len(handler.received) == 0

    async def test_empty_message_types_matches_all(self) -> None:
        """Handler with no message_types filter matches all types."""
        dispatcher = MessageDispatcher()
        handler = _RecordingHandler()
        dispatcher.register(handler)

        msg = _make_message(type=MessageType.ESCALATION)
        result = await dispatcher.dispatch(msg)

        assert result.handlers_matched == 1
        assert len(handler.received) == 1

    async def test_multiple_handlers_receive_message(self) -> None:
        """All matching handlers receive the message."""
        dispatcher = MessageDispatcher()
        h1 = _RecordingHandler()
        h2 = _RecordingHandler()
        dispatcher.register(h1)
        dispatcher.register(h2)

        msg = _make_message()
        result = await dispatcher.dispatch(msg)

        assert result.handlers_matched == 2
        assert result.handlers_succeeded == 2
        assert len(h1.received) == 1
        assert len(h2.received) == 1


# ── Priority Filtering ────────────────────────────────────────


@pytest.mark.unit
class TestPriorityFiltering:
    async def test_min_priority_filters_low(self) -> None:
        """Handler with HIGH min_priority ignores NORMAL messages."""
        dispatcher = MessageDispatcher()
        handler = _RecordingHandler()
        dispatcher.register(
            handler,
            min_priority=MessagePriority.HIGH,
        )

        msg = _make_message(priority=MessagePriority.NORMAL)
        result = await dispatcher.dispatch(msg)

        assert result.handlers_matched == 0
        assert len(handler.received) == 0

    async def test_min_priority_accepts_equal(self) -> None:
        """Handler matches messages at exactly its min_priority."""
        dispatcher = MessageDispatcher()
        handler = _RecordingHandler()
        dispatcher.register(
            handler,
            min_priority=MessagePriority.HIGH,
        )

        msg = _make_message(priority=MessagePriority.HIGH)
        result = await dispatcher.dispatch(msg)

        assert result.handlers_matched == 1
        assert len(handler.received) == 1

    async def test_min_priority_accepts_higher(self) -> None:
        """Handler matches messages above its min_priority."""
        dispatcher = MessageDispatcher()
        handler = _RecordingHandler()
        dispatcher.register(
            handler,
            min_priority=MessagePriority.HIGH,
        )

        msg = _make_message(priority=MessagePriority.URGENT)
        result = await dispatcher.dispatch(msg)

        assert result.handlers_matched == 1
        assert len(handler.received) == 1


# ── Combined Type + Priority Filtering ───────────────────────


@pytest.mark.unit
class TestCombinedFiltering:
    async def test_type_and_priority_both_must_match(self) -> None:
        """Handler with both type and priority filters requires both to match."""
        dispatcher = MessageDispatcher()
        handler = _RecordingHandler()
        dispatcher.register(
            handler,
            message_types=frozenset({MessageType.ESCALATION}),
            min_priority=MessagePriority.HIGH,
        )

        # Right type, wrong priority → no match
        msg_low = _make_message(
            type=MessageType.ESCALATION,
            priority=MessagePriority.LOW,
        )
        result_low = await dispatcher.dispatch(msg_low)
        assert result_low.handlers_matched == 0

        # Wrong type, right priority → no match
        msg_wrong_type = _make_message(
            type=MessageType.TASK_UPDATE,
            priority=MessagePriority.URGENT,
        )
        result_wrong = await dispatcher.dispatch(msg_wrong_type)
        assert result_wrong.handlers_matched == 0

        # Both match → handler receives message
        msg_match = _make_message(
            type=MessageType.ESCALATION,
            priority=MessagePriority.URGENT,
        )
        result_match = await dispatcher.dispatch(msg_match)
        assert result_match.handlers_matched == 1
        assert len(handler.received) == 1


# ── Error Isolation ───────────────────────────────────────────


@pytest.mark.unit
class TestErrorIsolation:
    async def test_failing_handler_does_not_block_others(self) -> None:
        """One handler raising does not prevent others from executing."""
        dispatcher = MessageDispatcher()
        good_handler = _RecordingHandler()
        bad_handler = _FailingHandler("handler failed")

        dispatcher.register(good_handler, name="good")
        dispatcher.register(bad_handler, name="bad")

        msg = _make_message()
        result = await dispatcher.dispatch(msg)

        assert result.handlers_matched == 2
        assert result.handlers_succeeded == 1
        assert result.handlers_failed == 1
        assert len(result.errors) == 1
        assert "handler failed" in result.errors[0]
        assert len(good_handler.received) == 1

    async def test_all_handlers_fail(self) -> None:
        """All handlers failing is reflected in the result."""
        dispatcher = MessageDispatcher()
        dispatcher.register(_FailingHandler("err1"), name="bad1")
        dispatcher.register(_FailingHandler("err2"), name="bad2")

        msg = _make_message()
        result = await dispatcher.dispatch(msg)

        assert result.handlers_matched == 2
        assert result.handlers_succeeded == 0
        assert result.handlers_failed == 2
        assert len(result.errors) == 2


# ── Concurrent Execution ─────────────────────────────────────


@pytest.mark.unit
class TestConcurrentExecution:
    async def test_handlers_run_concurrently(self) -> None:
        """Multiple handlers execute concurrently, not sequentially."""
        order: list[str] = []

        async def _slow(msg: Message) -> None:
            order.append("slow-start")
            await asyncio.sleep(0.05)
            order.append("slow-end")

        async def _fast(msg: Message) -> None:
            order.append("fast-start")
            await asyncio.sleep(0.01)
            order.append("fast-end")

        dispatcher = MessageDispatcher()
        dispatcher.register(_slow, name="slow")
        dispatcher.register(_fast, name="fast")

        msg = _make_message()
        result = await dispatcher.dispatch(msg)

        assert result.handlers_succeeded == 2
        # Both should have started before either finished
        starts = [i for i, x in enumerate(order) if x.endswith("-start")]
        ends = [i for i, x in enumerate(order) if x.endswith("-end")]
        assert len(starts) == 2
        assert len(ends) == 2
        # Both starts should come before the first end
        assert max(starts) < min(ends)


# ── DispatchResult ────────────────────────────────────────────


@pytest.mark.unit
class TestDispatchResult:
    async def test_accurate_counts(self) -> None:
        """DispatchResult accurately reflects handler outcomes."""
        dispatcher = MessageDispatcher()
        dispatcher.register(_RecordingHandler(), name="ok1")
        dispatcher.register(_RecordingHandler(), name="ok2")
        dispatcher.register(_FailingHandler("oops"), name="fail")

        msg = _make_message()
        result = await dispatcher.dispatch(msg)

        assert result.message_id == msg.id
        assert result.handlers_matched == 3
        assert result.handlers_succeeded == 2
        assert result.handlers_failed == 1
        assert len(result.errors) == 1

    def test_dispatch_result_is_frozen(self) -> None:
        """DispatchResult is immutable."""
        from uuid import uuid4

        result = DispatchResult(
            message_id=uuid4(),
            handlers_succeeded=1,
            handlers_failed=0,
        )
        with pytest.raises(ValidationError):
            result.handlers_matched = 5  # type: ignore[misc]


# ── No Handlers ───────────────────────────────────────────────


@pytest.mark.unit
class TestNoHandlers:
    async def test_dispatch_with_no_registered_handlers(self) -> None:
        """Dispatch returns zero-result when no handlers registered."""
        dispatcher = MessageDispatcher()
        msg = _make_message()
        result = await dispatcher.dispatch(msg)

        assert result.handlers_matched == 0
        assert result.handlers_succeeded == 0
        assert result.handlers_failed == 0
        assert result.errors == ()

    async def test_dispatch_with_no_matching_handlers(self) -> None:
        """Dispatch returns zero-result when no handlers match."""
        dispatcher = MessageDispatcher()
        dispatcher.register(
            _RecordingHandler(),
            message_types=frozenset({MessageType.QUESTION}),
        )

        msg = _make_message(type=MessageType.ANNOUNCEMENT)
        result = await dispatcher.dispatch(msg)

        assert result.handlers_matched == 0
        assert result.handlers_succeeded == 0
        assert result.handlers_failed == 0
