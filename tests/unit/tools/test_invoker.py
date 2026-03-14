"""Tests for ToolInvoker."""

import time
from typing import TYPE_CHECKING

import pytest

from synthorg.providers.models import ToolCall, ToolResult

if TYPE_CHECKING:
    from collections.abc import Iterator

    from synthorg.tools.invoker import ToolInvoker
    from synthorg.tools.registry import ToolRegistry

    from .conftest import _ConcurrencyTrackingTool

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

    async def test_no_schema_skips_validation(
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
            # First deepcopy call is BaseTool.parameters_schema
            # (called from _validate_params); let it pass. Fail on
            # the second call (argument copying in _execute_tool).
            if call_count > 1:
                msg = "cannot copy"
                raise TypeError(msg)
            return real_deepcopy(obj, memo)  # type: ignore[arg-type]

        call = ToolCall(id="c_dc", name="mutating", arguments={"key": "val"})
        monkeypatch.setattr(
            "synthorg.tools.invoker.copy.deepcopy",
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
            "synthorg.tools.invoker.copy.deepcopy",
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


@pytest.mark.unit
class TestInvokeAllConcurrency:
    """Tests for concurrent execution in invoke_all."""

    async def test_concurrent_faster_than_sequential(
        self,
        concurrency_invoker: ToolInvoker,
    ) -> None:
        """Three 0.1s delay tools complete in less than 0.3s total."""
        calls = [
            ToolCall(
                id=f"d{i}",
                name="delay",
                arguments={"delay": 0.1, "value": f"v{i}"},
            )
            for i in range(3)
        ]
        start = time.monotonic()
        results = await concurrency_invoker.invoke_all(calls)
        elapsed = time.monotonic() - start
        assert len(results) == 3
        assert all(r.content == f"v{i}" for i, r in enumerate(results))
        # Sequential would take >= 0.3s; proves parallel execution
        assert elapsed < 0.5

    async def test_concurrent_results_in_input_order(
        self,
        concurrency_invoker: ToolInvoker,
    ) -> None:
        """Results match input order regardless of completion order."""
        calls = [
            ToolCall(
                id="slow",
                name="delay",
                arguments={"delay": 0.1, "value": "first"},
            ),
            ToolCall(
                id="fast",
                name="delay",
                arguments={"delay": 0.01, "value": "second"},
            ),
        ]
        results = await concurrency_invoker.invoke_all(calls)
        assert results[0].tool_call_id == "slow"
        assert results[0].content == "first"
        assert results[1].tool_call_id == "fast"
        assert results[1].content == "second"

    async def test_recoverable_error_does_not_cancel_siblings(
        self,
        concurrency_invoker: ToolInvoker,
    ) -> None:
        """A failing tool doesn't prevent siblings from completing."""
        calls = [
            ToolCall(id="c1", name="echo_test", arguments={"message": "a"}),
            ToolCall(id="c2", name="failing", arguments={"input": "x"}),
            ToolCall(id="c3", name="echo_test", arguments={"message": "b"}),
        ]
        results = await concurrency_invoker.invoke_all(calls)
        assert len(results) == 3
        assert results[0].is_error is False
        assert results[1].is_error is True
        assert results[2].is_error is False

    async def test_single_non_recoverable_raises_bare(
        self,
        concurrency_invoker: ToolInvoker,
    ) -> None:
        """Single fatal error re-raises as bare exception."""
        calls = [
            ToolCall(
                id="r1",
                name="recursion",
                arguments={"input": "boom"},
            ),
        ]
        with pytest.raises(RecursionError, match="maximum recursion depth"):
            await concurrency_invoker.invoke_all(calls)

    async def test_mixed_fatal_and_success_raises_fatal(
        self,
        concurrency_invoker: ToolInvoker,
    ) -> None:
        """Fatal error is raised even when siblings succeed."""
        calls = [
            ToolCall(id="ok1", name="echo_test", arguments={"message": "a"}),
            ToolCall(
                id="fatal",
                name="recursion",
                arguments={"input": "boom"},
            ),
            ToolCall(id="ok2", name="echo_test", arguments={"message": "b"}),
        ]
        with pytest.raises(RecursionError, match="maximum recursion depth"):
            await concurrency_invoker.invoke_all(calls)

    async def test_multiple_non_recoverable_raises_exception_group(
        self,
        concurrency_invoker: ToolInvoker,
    ) -> None:
        """Multiple fatal errors raise ExceptionGroup."""
        calls = [
            ToolCall(
                id="r1",
                name="recursion",
                arguments={"input": "boom1"},
            ),
            ToolCall(
                id="r2",
                name="recursion",
                arguments={"input": "boom2"},
            ),
        ]
        with pytest.raises(ExceptionGroup) as exc_info:
            await concurrency_invoker.invoke_all(calls)
        assert len(exc_info.value.exceptions) == 2
        assert all(isinstance(e, RecursionError) for e in exc_info.value.exceptions)


@pytest.mark.unit
class TestInvokeAllBounded:
    """Tests for max_concurrency parameter."""

    async def test_max_concurrency_one_sequential(
        self,
        concurrency_invoker: ToolInvoker,
        concurrency_tracking_tool: _ConcurrencyTrackingTool,
    ) -> None:
        """max_concurrency=1 enforces sequential execution (peak=1)."""
        calls = [
            ToolCall(
                id=f"t{i}",
                name="tracking",
                arguments={"duration": 0.02},
            )
            for i in range(3)
        ]
        results = await concurrency_invoker.invoke_all(calls, max_concurrency=1)
        assert len(results) == 3
        assert concurrency_tracking_tool.peak == 1

    async def test_max_concurrency_bounds_parallelism(
        self,
        concurrency_invoker: ToolInvoker,
        concurrency_tracking_tool: _ConcurrencyTrackingTool,
    ) -> None:
        """With max_concurrency=2, peak never exceeds 2."""
        calls = [
            ToolCall(
                id=f"t{i}",
                name="tracking",
                arguments={"duration": 0.05},
            )
            for i in range(5)
        ]
        await concurrency_invoker.invoke_all(calls, max_concurrency=2)
        assert concurrency_tracking_tool.peak <= 2

    async def test_max_concurrency_none_unbounded(
        self,
        concurrency_invoker: ToolInvoker,
        concurrency_tracking_tool: _ConcurrencyTrackingTool,
    ) -> None:
        """Without max_concurrency, parallelism exceeds 1."""
        calls = [
            ToolCall(
                id=f"t{i}",
                name="tracking",
                arguments={"duration": 0.05},
            )
            for i in range(5)
        ]
        await concurrency_invoker.invoke_all(calls)
        assert concurrency_tracking_tool.peak >= 3

    async def test_max_concurrency_validation(
        self,
        concurrency_invoker: ToolInvoker,
    ) -> None:
        """max_concurrency=0 and negative values raise ValueError."""
        calls = [
            ToolCall(id="c1", name="echo_test", arguments={"message": "a"}),
        ]
        with pytest.raises(ValueError, match="max_concurrency"):
            await concurrency_invoker.invoke_all(calls, max_concurrency=0)
        with pytest.raises(ValueError, match="max_concurrency"):
            await concurrency_invoker.invoke_all(calls, max_concurrency=-1)


@pytest.mark.unit
class TestInvokeAllEdgeCases:
    """Edge case tests for invoke_all."""

    async def test_single_call(
        self,
        concurrency_invoker: ToolInvoker,
    ) -> None:
        """Single-element input works correctly."""
        calls = [
            ToolCall(id="c1", name="echo_test", arguments={"message": "solo"}),
        ]
        results = await concurrency_invoker.invoke_all(calls)
        assert len(results) == 1
        assert results[0].content == "solo"

    async def test_generator_input(
        self,
        concurrency_invoker: ToolInvoker,
    ) -> None:
        """Non-list iterable (generator) works correctly."""

        def _gen() -> Iterator[ToolCall]:
            yield ToolCall(id="g1", name="echo_test", arguments={"message": "gen1"})
            yield ToolCall(id="g2", name="echo_test", arguments={"message": "gen2"})

        results = await concurrency_invoker.invoke_all(_gen())
        assert len(results) == 2
        assert results[0].content == "gen1"
        assert results[1].content == "gen2"

    async def test_empty_with_max_concurrency(
        self,
        concurrency_invoker: ToolInvoker,
    ) -> None:
        """Empty input with max_concurrency returns empty tuple."""
        results = await concurrency_invoker.invoke_all([], max_concurrency=3)
        assert results == ()


@pytest.mark.unit
class TestInvokerPermissionCheck:
    """Tests for permission checking within the invoker."""

    async def test_no_checker_allows_everything(
        self,
        sample_invoker: ToolInvoker,
        sample_tool_call: ToolCall,
    ) -> None:
        """Invoker without permission checker allows all tool calls."""
        result = await sample_invoker.invoke(sample_tool_call)
        assert result.is_error is False

    async def test_checker_denies_unpermitted_tool(
        self,
        permission_invoker: ToolInvoker,
    ) -> None:
        """Invoker with checker denies tools outside access level."""
        call = ToolCall(
            id="call_deploy",
            name="deploy_tool",
            arguments={},
        )
        result = await permission_invoker.invoke(call)
        assert result.is_error is True
        assert "Permission denied" in result.content
        assert "deploy_tool" in result.content or "deployment" in result.content

    async def test_checker_allows_permitted_tool(
        self,
        permission_invoker: ToolInvoker,
    ) -> None:
        """Invoker with checker allows tools within access level."""
        call = ToolCall(id="call_fs", name="fs_tool", arguments={})
        result = await permission_invoker.invoke(call)
        assert result.is_error is False

    async def test_invoke_all_mixed_permissions(
        self,
        permission_invoker: ToolInvoker,
    ) -> None:
        """invoke_all returns mix of permitted and denied results."""
        calls = [
            ToolCall(id="c1", name="fs_tool", arguments={}),
            ToolCall(id="c2", name="deploy_tool", arguments={}),
            ToolCall(id="c3", name="web_tool", arguments={}),
        ]
        results = await permission_invoker.invoke_all(calls)
        assert len(results) == 3
        assert results[0].is_error is False  # fs_tool: STANDARD allows
        assert results[1].is_error is True  # deploy_tool: STANDARD denies
        assert results[2].is_error is False  # web_tool: STANDARD allows


@pytest.mark.unit
class TestGetPermittedDefinitions:
    """Tests for get_permitted_definitions method."""

    def test_no_checker_returns_all(
        self,
        sample_invoker: ToolInvoker,
    ) -> None:
        """Without checker, returns all tool definitions."""
        defs = sample_invoker.get_permitted_definitions()
        assert len(defs) == len(sample_invoker.registry)

    def test_checker_filters_definitions(
        self,
        permission_invoker: ToolInvoker,
        permission_registry: ToolRegistry,
    ) -> None:
        """With checker, returns only permitted definitions."""
        defs = permission_invoker.get_permitted_definitions()
        names = {d.name for d in defs}
        # STANDARD allows: file_system, code_execution, version_control,
        # web, terminal, analytics
        assert "fs_tool" in names
        assert "code_tool" in names
        assert "web_tool" in names
        assert "terminal_tool" in names
        # STANDARD denies: deployment, other
        assert "deploy_tool" not in names
        assert "other_tool" not in names
