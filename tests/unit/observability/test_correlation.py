"""Tests for correlation ID management."""

import uuid

import pytest
import structlog

from synthorg.observability.correlation import (
    bind_correlation_id,
    clear_correlation_ids,
    generate_correlation_id,
    unbind_correlation_id,
    with_correlation,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestGenerateCorrelationId:
    """Tests for generate_correlation_id."""

    def test_returns_string(self) -> None:
        cid = generate_correlation_id()
        assert isinstance(cid, str)

    def test_is_valid_uuid4(self) -> None:
        cid = generate_correlation_id()
        parsed = uuid.UUID(cid)
        assert parsed.version == 4

    def test_unique_values(self) -> None:
        ids = {generate_correlation_id() for _ in range(100)}
        assert len(ids) == 100


@pytest.mark.unit
class TestBindCorrelationId:
    """Tests for bind_correlation_id."""

    def test_bind_request_id(self) -> None:
        bind_correlation_id(request_id="req-1")
        ctx = structlog.contextvars.get_contextvars()
        assert ctx["request_id"] == "req-1"

    def test_bind_task_id(self) -> None:
        bind_correlation_id(task_id="task-1")
        ctx = structlog.contextvars.get_contextvars()
        assert ctx["task_id"] == "task-1"

    def test_bind_agent_id(self) -> None:
        bind_correlation_id(agent_id="agent-1")
        ctx = structlog.contextvars.get_contextvars()
        assert ctx["agent_id"] == "agent-1"

    def test_bind_multiple(self) -> None:
        bind_correlation_id(request_id="r", task_id="t", agent_id="a")
        ctx = structlog.contextvars.get_contextvars()
        assert ctx["request_id"] == "r"
        assert ctx["task_id"] == "t"
        assert ctx["agent_id"] == "a"

    def test_bind_none_skipped(self) -> None:
        bind_correlation_id(request_id="r")
        bind_correlation_id()
        ctx = structlog.contextvars.get_contextvars()
        assert ctx["request_id"] == "r"

    def test_bind_overwrites_existing(self) -> None:
        bind_correlation_id(request_id="old")
        bind_correlation_id(request_id="new")
        ctx = structlog.contextvars.get_contextvars()
        assert ctx["request_id"] == "new"


@pytest.mark.unit
class TestUnbindCorrelationId:
    """Tests for unbind_correlation_id."""

    def test_unbind_request_id(self) -> None:
        bind_correlation_id(request_id="r", task_id="t")
        unbind_correlation_id(request_id=True)
        ctx = structlog.contextvars.get_contextvars()
        assert "request_id" not in ctx
        assert ctx["task_id"] == "t"

    def test_unbind_multiple(self) -> None:
        bind_correlation_id(request_id="r", task_id="t", agent_id="a")
        unbind_correlation_id(request_id=True, agent_id=True)
        ctx = structlog.contextvars.get_contextvars()
        assert "request_id" not in ctx
        assert "agent_id" not in ctx
        assert ctx["task_id"] == "t"

    def test_unbind_false_keeps_key(self) -> None:
        bind_correlation_id(request_id="r")
        unbind_correlation_id(request_id=False)
        ctx = structlog.contextvars.get_contextvars()
        assert ctx["request_id"] == "r"

    def test_unbind_nonexistent_key_is_safe(self) -> None:
        unbind_correlation_id(request_id=True)


@pytest.mark.unit
class TestClearCorrelationIds:
    """Tests for clear_correlation_ids."""

    def test_clears_all_correlation_keys(self) -> None:
        bind_correlation_id(request_id="r", task_id="t", agent_id="a")
        clear_correlation_ids()
        ctx = structlog.contextvars.get_contextvars()
        assert "request_id" not in ctx
        assert "task_id" not in ctx
        assert "agent_id" not in ctx

    def test_preserves_non_correlation_context(self) -> None:
        structlog.contextvars.bind_contextvars(custom_key="keep-me")
        bind_correlation_id(request_id="r")
        clear_correlation_ids()
        ctx = structlog.contextvars.get_contextvars()
        assert ctx["custom_key"] == "keep-me"
        assert "request_id" not in ctx


@pytest.mark.unit
class TestWithCorrelation:
    """Tests for the with_correlation decorator."""

    def test_binds_during_execution(self) -> None:
        captured: dict[str, str] = {}

        @with_correlation(request_id="req-42")
        def inner() -> None:
            ctx = structlog.contextvars.get_contextvars()
            captured.update(ctx)

        inner()
        assert captured["request_id"] == "req-42"

    def test_unbinds_after_execution(self) -> None:
        @with_correlation(request_id="req-42")
        def inner() -> None:
            pass

        inner()
        ctx = structlog.contextvars.get_contextvars()
        assert "request_id" not in ctx

    def test_unbinds_on_exception(self) -> None:
        @with_correlation(request_id="req-err")
        def inner() -> None:
            msg = "boom"
            raise RuntimeError(msg)

        with pytest.raises(RuntimeError, match="boom"):
            inner()
        ctx = structlog.contextvars.get_contextvars()
        assert "request_id" not in ctx

    def test_preserves_return_value(self) -> None:
        @with_correlation(task_id="t-1")
        def inner() -> int:
            return 42

        assert inner() == 42

    def test_binds_multiple_ids(self) -> None:
        captured: dict[str, str] = {}

        @with_correlation(request_id="r", task_id="t", agent_id="a")
        def inner() -> None:
            ctx = structlog.contextvars.get_contextvars()
            captured.update(ctx)

        inner()
        assert captured["request_id"] == "r"
        assert captured["task_id"] == "t"
        assert captured["agent_id"] == "a"

    def test_preserves_function_name(self) -> None:
        @with_correlation(request_id="r")
        def my_func() -> None:
            pass

        assert my_func.__name__ == "my_func"

    def test_forwards_arguments(self) -> None:
        @with_correlation(task_id="t-1")
        def add(a: int, b: int) -> int:
            return a + b

        assert add(3, 4) == 7

    def test_preserves_outer_context(self) -> None:
        bind_correlation_id(request_id="outer")

        @with_correlation(request_id="inner")
        def inner() -> str:
            ctx = structlog.contextvars.get_contextvars()
            return ctx["request_id"]  # type: ignore[no-any-return]

        assert inner() == "inner"
        ctx = structlog.contextvars.get_contextvars()
        assert ctx["request_id"] == "outer"

    def test_rejects_async_function(self) -> None:
        with pytest.raises(TypeError, match="does not support async"):

            @with_correlation(request_id="r")
            async def async_fn() -> None:
                pass
