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


@pytest.mark.unit
class TestInvokeNonRecoverableErrors:
    """Tests for MemoryError/RecursionError re-raise behavior."""

    async def test_recursion_error_propagates(
        self,
        extended_invoker: ToolInvoker,
    ) -> None:
        call = ToolCall(
            id="call_recursion",
            name="recursion",
            arguments={"input": "test"},
        )
        with pytest.raises(RecursionError, match="maximum recursion depth"):
            await extended_invoker.invoke(call)

    async def test_recursion_error_not_swallowed_as_tool_result(
        self,
        extended_invoker: ToolInvoker,
    ) -> None:
        call = ToolCall(
            id="call_recursion2",
            name="recursion",
            arguments={"input": "test"},
        )
        with pytest.raises(RecursionError):
            await extended_invoker.invoke(call)


@pytest.mark.unit
class TestInvokeSchemaError:
    """Tests for invalid tool schema (SchemaError) handling."""

    async def test_invalid_schema_returns_error(
        self,
        extended_invoker: ToolInvoker,
    ) -> None:
        call = ToolCall(
            id="call_bad_schema",
            name="invalid_schema",
            arguments={"data": "test"},
        )
        result = await extended_invoker.invoke(call)
        assert result.is_error is True
        assert result.tool_call_id == "call_bad_schema"

    async def test_invalid_schema_does_not_raise(
        self,
        extended_invoker: ToolInvoker,
    ) -> None:
        call = ToolCall(
            id="call_bad_schema2",
            name="invalid_schema",
            arguments={"data": "test"},
        )
        result = await extended_invoker.invoke(call)
        assert isinstance(result, ToolResult)


@pytest.mark.unit
class TestInvokeSsrfProtection:
    """Tests for SSRF prevention via blocked remote $ref resolution."""

    async def test_remote_ref_blocked(
        self,
        extended_invoker: ToolInvoker,
    ) -> None:
        call = ToolCall(
            id="call_ssrf",
            name="remote_ref",
            arguments={"data": "test"},
        )
        result = await extended_invoker.invoke(call)
        assert result.is_error is True
        assert result.tool_call_id == "call_ssrf"

    async def test_remote_ref_does_not_raise(
        self,
        extended_invoker: ToolInvoker,
    ) -> None:
        call = ToolCall(
            id="call_ssrf2",
            name="remote_ref",
            arguments={"data": "test"},
        )
        result = await extended_invoker.invoke(call)
        assert isinstance(result, ToolResult)


@pytest.mark.unit
class TestInvokeBoundaryIsolation:
    """Tests that tool execution receives isolated argument copies."""

    async def test_tool_receives_deep_copy_of_arguments(
        self,
        extended_invoker: ToolInvoker,
    ) -> None:
        """Nested argument structures are isolated from the frozen model."""
        call = ToolCall(
            id="c1",
            name="mutating",
            arguments={"nested": {"key": "original"}},
        )
        await extended_invoker.invoke(call)
        assert call.arguments["nested"]["key"] == "original"
        assert "mutated" not in call.arguments.get("nested", {})
        assert "injected" not in call.arguments

    async def test_nested_mutation_does_not_leak(
        self,
        extended_invoker: ToolInvoker,
    ) -> None:
        """Tool mutating nested dicts does not affect the original ToolCall."""
        call = ToolCall(
            id="c2",
            name="mutating",
            arguments={"nested": {"value": 42}},
        )
        await extended_invoker.invoke(call)
        assert "mutated" not in call.arguments.get("nested", {})


@pytest.mark.unit
class TestInvokeDeepcopyFailure:
    """Tests for argument deep-copy failure handling."""

    async def test_deepcopy_failure_returns_error_result(
        self,
        extended_invoker: ToolInvoker,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When deepcopy of arguments fails, a ToolResult error is returned."""
        import copy as _copy_mod

        real_deepcopy = _copy_mod.deepcopy
        call_count = 0

        def _fail_on_execute(obj: object, memo: object = None) -> object:
            nonlocal call_count
            call_count += 1
            # First deepcopy call is in _validate_params via
            # parameters_schema; let it pass. Fail on the second
            # call in _execute_tool.
            if call_count > 1:
                msg = "cannot copy"
                raise TypeError(msg)
            return real_deepcopy(obj, memo)  # type: ignore[arg-type]

        call = ToolCall(id="c_dc", name="mutating", arguments={"key": "val"})
        monkeypatch.setattr(
            "ai_company.tools.invoker.copy.deepcopy",
            _fail_on_execute,
        )
        result = await extended_invoker.invoke(call)
        assert result.is_error is True
        assert "safely copied" in result.content
        assert result.tool_call_id == "c_dc"

    async def test_recursion_error_during_deepcopy_propagates(
        self,
        extended_invoker: ToolInvoker,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """RecursionError during deepcopy is re-raised, not swallowed."""
        import copy as _copy_mod

        real_deepcopy = _copy_mod.deepcopy
        call_count = 0

        def _fail_on_execute(obj: object, memo: object = None) -> object:
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                msg = "maximum recursion depth exceeded"
                raise RecursionError(msg)
            return real_deepcopy(obj, memo)  # type: ignore[arg-type]

        call = ToolCall(id="c_rec", name="mutating", arguments={"key": "val"})
        monkeypatch.setattr(
            "ai_company.tools.invoker.copy.deepcopy",
            _fail_on_execute,
        )
        with pytest.raises(RecursionError, match="maximum recursion depth"):
            await extended_invoker.invoke(call)


@pytest.mark.unit
class TestInvokeEmptyErrorMessage:
    """Tests for empty exception message fallback."""

    async def test_empty_error_message_fallback(
        self,
        extended_invoker: ToolInvoker,
    ) -> None:
        call = ToolCall(
            id="call_empty_err",
            name="empty_error",
            arguments={"input": "test"},
        )
        result = await extended_invoker.invoke(call)
        assert result.is_error is True
        assert "ValueError (no message)" in result.content
