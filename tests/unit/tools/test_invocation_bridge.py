"""Tests for the tool invocation bridge (best-effort recording)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.tools.invocation_bridge import record_tool_invocation
from synthorg.tools.invocation_tracker import ToolInvocationTracker

_TEST_STARTED_AT = datetime.now(UTC)


def _make_invoker(
    *,
    tracker: ToolInvocationTracker | None = None,
    agent_id: str | None = "agent-001",
    task_id: str | None = "task-001",
) -> MagicMock:
    """Build a mock ToolInvoker with private attrs the bridge accesses."""
    invoker = MagicMock()
    invoker._invocation_tracker = tracker
    invoker._agent_id = agent_id
    invoker._task_id = task_id
    return invoker


def _make_tool_call(
    *,
    call_id: str = "call-001",
    name: str = "read_file",
) -> MagicMock:
    call = MagicMock()
    call.id = call_id
    call.name = name
    return call


def _make_result(*, is_error: bool = False, content: str = "ok") -> MagicMock:
    result = MagicMock()
    result.is_error = is_error
    result.content = content
    return result


@pytest.mark.unit
class TestRecordToolInvocation:
    async def test_happy_path_records_invocation(self) -> None:
        tracker = ToolInvocationTracker()
        invoker = _make_invoker(tracker=tracker)
        tool_call = _make_tool_call()
        result = _make_result()

        await record_tool_invocation(
            invoker,
            tool_call,
            result,
            started_at=_TEST_STARTED_AT,
        )

        records = await tracker.get_records()
        assert len(records) == 1
        assert records[0].agent_id == "agent-001"
        assert records[0].tool_name == "read_file"
        assert records[0].is_success is True
        assert records[0].error_message is None

    async def test_error_result_stores_error_message(self) -> None:
        tracker = ToolInvocationTracker()
        invoker = _make_invoker(tracker=tracker)
        tool_call = _make_tool_call(name="write_file")
        result = _make_result(is_error=True, content="Permission denied")

        await record_tool_invocation(
            invoker,
            tool_call,
            result,
            started_at=_TEST_STARTED_AT,
        )

        records = await tracker.get_records()
        assert len(records) == 1
        assert records[0].is_success is False
        assert records[0].error_message == "Permission denied"

    async def test_early_return_when_tracker_is_none(self) -> None:
        invoker = _make_invoker(tracker=None)
        tool_call = _make_tool_call()
        result = _make_result()

        # Should not raise
        await record_tool_invocation(
            invoker,
            tool_call,
            result,
            started_at=_TEST_STARTED_AT,
        )

    async def test_early_return_when_agent_id_is_none(self) -> None:
        tracker = ToolInvocationTracker()
        invoker = _make_invoker(tracker=tracker, agent_id=None)
        tool_call = _make_tool_call()
        result = _make_result()

        await record_tool_invocation(
            invoker,
            tool_call,
            result,
            started_at=_TEST_STARTED_AT,
        )

        records = await tracker.get_records()
        assert records == ()

    async def test_exception_in_tracker_silently_degrades(self) -> None:
        tracker = AsyncMock(spec=ToolInvocationTracker)
        tracker.record.side_effect = RuntimeError("storage failure")
        invoker = _make_invoker(tracker=tracker)
        tool_call = _make_tool_call()
        result = _make_result()

        # Should not raise
        await record_tool_invocation(
            invoker,
            tool_call,
            result,
            started_at=_TEST_STARTED_AT,
        )

    @pytest.mark.parametrize(
        "exc_class",
        [MemoryError, RecursionError],
        ids=["memory_error", "recursion_error"],
    )
    async def test_fatal_error_propagates(
        self,
        exc_class: type[BaseException],
    ) -> None:
        tracker = AsyncMock(spec=ToolInvocationTracker)
        tracker.record.side_effect = exc_class("fatal")
        invoker = _make_invoker(tracker=tracker)
        tool_call = _make_tool_call()
        result = _make_result()

        with pytest.raises(exc_class):
            await record_tool_invocation(
                invoker,
                tool_call,
                result,
                started_at=_TEST_STARTED_AT,
            )

    async def test_error_message_truncated_to_2048(self) -> None:
        tracker = ToolInvocationTracker()
        invoker = _make_invoker(tracker=tracker)
        tool_call = _make_tool_call()
        long_content = "x" * 5000
        result = _make_result(is_error=True, content=long_content)

        await record_tool_invocation(
            invoker,
            tool_call,
            result,
            started_at=_TEST_STARTED_AT,
        )

        records = await tracker.get_records()
        assert len(records) == 1
        assert len(records[0].error_message) == 2048  # type: ignore[arg-type]
