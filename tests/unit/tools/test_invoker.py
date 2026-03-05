"""Tests for ToolInvoker."""

from typing import TYPE_CHECKING

import pytest

from ai_company.providers.models import ToolCall, ToolResult

if TYPE_CHECKING:
    from ai_company.tools.invoker import ToolInvoker

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestInvokeSuccess:
    """Tests for successful tool invocation."""

    async def test_invoke_returns_tool_result(
        self,
        sample_invoker: ToolInvoker,
        sample_tool_call: ToolCall,
    ) -> None:
        result = await sample_invoker.invoke(sample_tool_call)
        assert isinstance(result, ToolResult)
        assert result.content == "hello"
        assert result.is_error is False

    async def test_tool_call_id_matches(
        self,
        sample_invoker: ToolInvoker,
        sample_tool_call: ToolCall,
    ) -> None:
        result = await sample_invoker.invoke(sample_tool_call)
        assert result.tool_call_id == sample_tool_call.id


@pytest.mark.unit
class TestInvokeNotFound:
    """Tests for tool-not-found handling."""

    async def test_not_found_returns_error_result(
        self,
        sample_invoker: ToolInvoker,
    ) -> None:
        call = ToolCall(id="call_x", name="nonexistent", arguments={})
        result = await sample_invoker.invoke(call)
        assert result.is_error is True
        assert result.tool_call_id == "call_x"
        assert "not registered" in result.content

    async def test_not_found_does_not_raise(
        self,
        sample_invoker: ToolInvoker,
    ) -> None:
        call = ToolCall(id="call_x", name="nonexistent", arguments={})
        result = await sample_invoker.invoke(call)
        assert isinstance(result, ToolResult)


@pytest.mark.unit
class TestInvokeParameterValidation:
    """Tests for parameter schema validation."""

    async def test_valid_params_accepted(
        self,
        sample_invoker: ToolInvoker,
    ) -> None:
        call = ToolCall(
            id="call_strict",
            name="strict",
            arguments={"query": "hello", "limit": 10},
        )
        result = await sample_invoker.invoke(call)
        assert result.is_error is False
        assert "query=hello" in result.content

    async def test_invalid_params_returns_error(
        self,
        sample_invoker: ToolInvoker,
    ) -> None:
        call = ToolCall(
            id="call_bad",
            name="strict",
            arguments={"query": "hello", "limit": "not_a_number"},
        )
        result = await sample_invoker.invoke(call)
        assert result.is_error is True
        assert result.tool_call_id == "call_bad"

    async def test_missing_required_params_returns_error(
        self,
        sample_invoker: ToolInvoker,
    ) -> None:
        call = ToolCall(
            id="call_missing",
            name="strict",
            arguments={"query": "hello"},
        )
        result = await sample_invoker.invoke(call)
        assert result.is_error is True

    async def test_extra_params_returns_error(
        self,
        sample_invoker: ToolInvoker,
    ) -> None:
        call = ToolCall(
            id="call_extra",
            name="echo_test",
            arguments={"message": "hi", "extra": "nope"},
        )
        result = await sample_invoker.invoke(call)
        assert result.is_error is True

    async def test_empty_schema_skips_validation(
        self,
        sample_invoker: ToolInvoker,
    ) -> None:
        call = ToolCall(
            id="call_noschema",
            name="no_schema",
            arguments={"anything": "goes"},
        )
        result = await sample_invoker.invoke(call)
        assert result.is_error is False


@pytest.mark.unit
class TestInvokeSoftError:
    """Tests for tool-reported soft errors (is_error=True without exception)."""

    async def test_soft_error_propagated(
        self,
        sample_invoker: ToolInvoker,
    ) -> None:
        call = ToolCall(
            id="call_soft",
            name="soft_error",
            arguments={"input": "test"},
        )
        result = await sample_invoker.invoke(call)
        assert result.is_error is True
        assert result.content == "soft fail"
        assert result.tool_call_id == "call_soft"


@pytest.mark.unit
class TestInvokeExecutionError:
    """Tests for execution error handling."""

    async def test_execution_error_caught(
        self,
        sample_invoker: ToolInvoker,
    ) -> None:
        call = ToolCall(
            id="call_fail",
            name="failing",
            arguments={"input": "test"},
        )
        result = await sample_invoker.invoke(call)
        assert result.is_error is True
        assert result.tool_call_id == "call_fail"
        assert "tool execution failed" in result.content

    async def test_execution_error_does_not_propagate(
        self,
        sample_invoker: ToolInvoker,
    ) -> None:
        call = ToolCall(
            id="call_fail2",
            name="failing",
            arguments={"input": "test"},
        )
        result = await sample_invoker.invoke(call)
        assert isinstance(result, ToolResult)


@pytest.mark.unit
class TestInvokeAll:
    """Tests for invoke_all method."""

    async def test_invoke_all_empty(
        self,
        sample_invoker: ToolInvoker,
    ) -> None:
        results = await sample_invoker.invoke_all([])
        assert results == ()

    async def test_invoke_all_multiple(
        self,
        sample_invoker: ToolInvoker,
    ) -> None:
        calls = [
            ToolCall(id="c1", name="echo_test", arguments={"message": "a"}),
            ToolCall(id="c2", name="echo_test", arguments={"message": "b"}),
        ]
        results = await sample_invoker.invoke_all(calls)
        assert len(results) == 2
        assert results[0].content == "a"
        assert results[1].content == "b"

    async def test_invoke_all_mixed_success_and_error(
        self,
        sample_invoker: ToolInvoker,
    ) -> None:
        calls = [
            ToolCall(id="c1", name="echo_test", arguments={"message": "ok"}),
            ToolCall(id="c2", name="failing", arguments={"input": "x"}),
            ToolCall(id="c3", name="echo_test", arguments={"message": "also ok"}),
        ]
        results = await sample_invoker.invoke_all(calls)
        assert len(results) == 3
        assert results[0].is_error is False
        assert results[1].is_error is True
        assert results[2].is_error is False

    async def test_invoke_all_preserves_order(
        self,
        sample_invoker: ToolInvoker,
    ) -> None:
        calls = [
            ToolCall(id="c1", name="echo_test", arguments={"message": "first"}),
            ToolCall(id="c2", name="echo_test", arguments={"message": "second"}),
            ToolCall(id="c3", name="echo_test", arguments={"message": "third"}),
        ]
        results = await sample_invoker.invoke_all(calls)
        assert results[0].tool_call_id == "c1"
        assert results[1].tool_call_id == "c2"
        assert results[2].tool_call_id == "c3"
