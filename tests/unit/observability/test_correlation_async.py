"""Tests for the async correlation decorator."""

import pytest
import structlog

from synthorg.observability.correlation import with_correlation_async

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestWithCorrelationAsync:
    async def test_binds_during_execution(self) -> None:
        captured: dict[str, str] = {}

        @with_correlation_async(request_id="req-99", task_id="task-7")
        async def _inner() -> None:
            ctx = structlog.contextvars.get_contextvars()
            captured.update(ctx)

        await _inner()
        assert captured["request_id"] == "req-99"
        assert captured["task_id"] == "task-7"

    async def test_unbinds_after_execution(self) -> None:
        @with_correlation_async(request_id="req-1")
        async def _inner() -> None:
            pass

        await _inner()
        ctx = structlog.contextvars.get_contextvars()
        assert "request_id" not in ctx

    async def test_unbinds_on_exception(self) -> None:
        @with_correlation_async(request_id="req-err")
        async def _inner() -> None:
            msg = "boom"
            raise RuntimeError(msg)

        with pytest.raises(RuntimeError, match="boom"):
            await _inner()

        ctx = structlog.contextvars.get_contextvars()
        assert "request_id" not in ctx

    async def test_preserves_return_value(self) -> None:
        @with_correlation_async(agent_id="agent-42")
        async def _inner() -> int:
            return 42

        assert await _inner() == 42

    def test_rejects_sync_function(self) -> None:
        with pytest.raises(TypeError, match="requires an async function"):

            @with_correlation_async(request_id="req-1")  # type: ignore[arg-type]
            def _sync() -> None:
                pass

    async def test_only_binds_non_none_ids(self) -> None:
        captured: dict[str, str] = {}

        @with_correlation_async(request_id="req-only")
        async def _inner() -> None:
            captured.update(structlog.contextvars.get_contextvars())

        await _inner()
        assert "request_id" in captured
        assert "task_id" not in captured
        assert "agent_id" not in captured
