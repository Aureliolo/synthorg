"""Tests for extracted loop helper functions."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.budget.call_category import LLMCallCategory
from synthorg.core.enums import ToolCategory
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_helpers import (
    build_result,
    call_provider,
    check_budget,
    check_response_errors,
    check_shutdown,
    clear_last_turn_tool_calls,
    execute_tool_calls,
    get_tool_definitions,
    make_turn_record,
    response_to_message,
)
from synthorg.engine.loop_protocol import TerminationReason, TurnRecord
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import (
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolCall,
)
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry


def _usage(
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> TokenUsage:
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=0.001,
    )


def _stop_response(content: str = "Done.") -> CompletionResponse:
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=_usage(),
        model="test-model-001",
    )


def _tool_use_response(
    tool_name: str = "echo",
    tool_call_id: str = "tc-1",
) -> CompletionResponse:
    return CompletionResponse(
        content=None,
        tool_calls=(ToolCall(id=tool_call_id, name=tool_name, arguments={}),),
        finish_reason=FinishReason.TOOL_USE,
        usage=_usage(),
        model="test-model-001",
    )


class _StubTool(BaseTool):
    def __init__(self, name: str = "echo") -> None:
        super().__init__(
            name=name,
            description="Test tool",
            category=ToolCategory.CODE_EXECUTION,
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            content=f"echoed: {arguments}",
            is_error=False,
        )


def _make_invoker(*tool_names: str) -> ToolInvoker:
    tools = [_StubTool(name=n) for n in tool_names]
    return ToolInvoker(ToolRegistry(tools))


def _ctx_with_user_msg(ctx: AgentContext) -> AgentContext:
    from synthorg.providers.models import ChatMessage

    msg = ChatMessage(role=MessageRole.USER, content="Do something")
    return ctx.with_message(msg)


# ── check_shutdown ──────────────────────────────────────────────────


@pytest.mark.unit
class TestCheckShutdown:
    def test_none_checker_returns_none(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        assert check_shutdown(sample_agent_context, None, []) is None

    def test_false_returns_none(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        assert (
            check_shutdown(
                sample_agent_context,
                lambda: False,
                [],
            )
            is None
        )

    def test_true_returns_shutdown_result(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = check_shutdown(
            sample_agent_context,
            lambda: True,
            [],
        )
        assert result is not None
        assert result.termination_reason == TerminationReason.SHUTDOWN

    def test_exception_returns_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        def bad() -> bool:
            msg = "broken"
            raise ValueError(msg)

        result = check_shutdown(sample_agent_context, bad, [])
        assert result is not None
        assert result.termination_reason == TerminationReason.ERROR
        assert "Shutdown checker failed" in (result.error_message or "")

    def test_memory_error_propagates(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        def oom() -> bool:
            raise MemoryError

        with pytest.raises(MemoryError):
            check_shutdown(sample_agent_context, oom, [])


# ── check_budget ────────────────────────────────────────────────────


@pytest.mark.unit
class TestCheckBudget:
    def test_none_checker_returns_none(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        assert check_budget(sample_agent_context, None, []) is None

    def test_not_exhausted_returns_none(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        assert (
            check_budget(
                sample_agent_context,
                lambda _: False,
                [],
            )
            is None
        )

    def test_exhausted_returns_budget_result(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = check_budget(
            sample_agent_context,
            lambda _: True,
            [],
        )
        assert result is not None
        assert result.termination_reason == TerminationReason.BUDGET_EXHAUSTED

    def test_exception_returns_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        def bad(_: AgentContext) -> bool:
            msg = "db error"
            raise ConnectionError(msg)

        result = check_budget(sample_agent_context, bad, [])
        assert result is not None
        assert result.termination_reason == TerminationReason.ERROR

    def test_memory_error_propagates(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        def oom(_: AgentContext) -> bool:
            raise MemoryError

        with pytest.raises(MemoryError):
            check_budget(sample_agent_context, oom, [])

    def test_recursion_error_propagates(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        def recurse(_: AgentContext) -> bool:
            raise RecursionError

        with pytest.raises(RecursionError):
            check_budget(sample_agent_context, recurse, [])


# ── call_provider ───────────────────────────────────────────────────


@pytest.mark.unit
class TestCallProvider:
    async def test_success(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        expected = _stop_response("ok")
        provider = mock_provider_factory([expected])
        config = CompletionConfig(temperature=0.5)

        result = await call_provider(
            ctx,
            provider,
            "test-model",
            None,
            config,
            1,
            [],
        )
        assert isinstance(result, CompletionResponse)
        assert result.content == "ok"

    async def test_provider_exception_returns_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)

        class _Failing:
            async def complete(self, *a: Any, **kw: Any) -> None:
                msg = "connection refused"
                raise ConnectionError(msg)

        result = await call_provider(
            ctx,
            _Failing(),  # type: ignore[arg-type]
            "m",
            None,
            CompletionConfig(),
            1,
            [],
        )
        from synthorg.engine.loop_protocol import ExecutionResult

        assert isinstance(result, ExecutionResult)
        assert result.termination_reason == TerminationReason.ERROR

    async def test_memory_error_propagates(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)

        class _OOM:
            async def complete(self, *a: Any, **kw: Any) -> None:
                raise MemoryError

        with pytest.raises(MemoryError):
            await call_provider(
                ctx,
                _OOM(),  # type: ignore[arg-type]
                "m",
                None,
                CompletionConfig(),
                1,
                [],
            )


# ── check_response_errors ──────────────────────────────────────────


@pytest.mark.unit
class TestCheckResponseErrors:
    def test_stop_returns_none(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        response = _stop_response()
        assert (
            check_response_errors(
                sample_agent_context,
                response,
                1,
                [],
            )
            is None
        )

    def test_content_filter_returns_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        response = CompletionResponse(
            content=None,
            finish_reason=FinishReason.CONTENT_FILTER,
            usage=_usage(),
            model="test-model-001",
        )
        result = check_response_errors(
            sample_agent_context,
            response,
            1,
            [],
        )
        assert result is not None
        assert result.termination_reason == TerminationReason.ERROR
        assert "content_filter" in (result.error_message or "")

    def test_error_finish_reason_returns_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        response = CompletionResponse(
            content=None,
            finish_reason=FinishReason.ERROR,
            usage=_usage(),
            model="test-model-001",
        )
        result = check_response_errors(
            sample_agent_context,
            response,
            1,
            [],
        )
        assert result is not None
        assert result.termination_reason == TerminationReason.ERROR

    def test_cost_included_in_error_context(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        response = CompletionResponse(
            content=None,
            finish_reason=FinishReason.CONTENT_FILTER,
            usage=_usage(100, 50),
            model="test-model-001",
        )
        result = check_response_errors(
            sample_agent_context,
            response,
            1,
            [],
        )
        assert result is not None
        assert result.context.turn_count == 1


# ── execute_tool_calls ──────────────────────────────────────────────


@pytest.mark.unit
class TestExecuteToolCalls:
    async def test_no_invoker_returns_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = _tool_use_response()
        result = await execute_tool_calls(
            ctx,
            None,
            response,
            1,
            [],
        )
        from synthorg.engine.loop_protocol import ExecutionResult

        assert isinstance(result, ExecutionResult)
        assert result.termination_reason == TerminationReason.ERROR
        assert "no tool invoker" in (result.error_message or "")

    async def test_successful_tool_execution(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = _tool_use_response("echo")
        invoker = _make_invoker("echo")

        result = await execute_tool_calls(
            ctx,
            invoker,
            response,
            1,
            [],
        )
        assert isinstance(result, AgentContext)
        # Should have tool result message appended
        last_msg = result.conversation[-1]
        assert last_msg.role == MessageRole.TOOL

    async def test_invoke_all_exception_returns_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = _tool_use_response()
        mock_invoker = MagicMock()
        mock_invoker.invoke_all = AsyncMock(
            side_effect=RuntimeError("boom"),
        )

        result = await execute_tool_calls(
            ctx,
            mock_invoker,
            response,
            1,
            [],
        )
        from synthorg.engine.loop_protocol import ExecutionResult

        assert isinstance(result, ExecutionResult)
        assert "Tool execution failed" in (result.error_message or "")

    async def test_memory_error_propagates(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = _tool_use_response()
        mock_invoker = MagicMock()
        mock_invoker.invoke_all = AsyncMock(side_effect=MemoryError)

        with pytest.raises(MemoryError):
            await execute_tool_calls(
                ctx,
                mock_invoker,
                response,
                1,
                [],
            )


# ── get_tool_definitions ────────────────────────────────────────────


@pytest.mark.unit
class TestGetToolDefinitions:
    def test_none_invoker_returns_none(self) -> None:
        assert get_tool_definitions(None) is None

    def test_empty_registry_returns_none(self) -> None:
        invoker = ToolInvoker(ToolRegistry([]))
        assert get_tool_definitions(invoker) is None

    def test_returns_definitions(self) -> None:
        invoker = _make_invoker("echo", "search")
        defs = get_tool_definitions(invoker)
        assert defs is not None
        assert len(defs) == 2


# ── response_to_message ─────────────────────────────────────────────


@pytest.mark.unit
class TestResponseToMessage:
    def test_basic_message(self) -> None:
        response = _stop_response("Hello")
        msg = response_to_message(response)
        assert msg.role == MessageRole.ASSISTANT
        assert msg.content == "Hello"
        assert msg.tool_calls == ()

    def test_with_tool_calls(self) -> None:
        response = _tool_use_response("echo")
        msg = response_to_message(response)
        assert msg.role == MessageRole.ASSISTANT
        assert len(msg.tool_calls) == 1


# ── make_turn_record ────────────────────────────────────────────────


@pytest.mark.unit
class TestMakeTurnRecord:
    def test_basic(self) -> None:
        response = _stop_response()
        record = make_turn_record(1, response)
        assert record.turn_number == 1
        assert record.input_tokens == 10
        assert record.output_tokens == 5
        assert record.cost_usd == 0.001
        assert record.tool_calls_made == ()
        assert record.finish_reason == FinishReason.STOP
        assert record.call_category is None

    def test_with_tool_calls(self) -> None:
        response = _tool_use_response("echo")
        record = make_turn_record(2, response)
        assert record.tool_calls_made == ("echo",)
        assert record.finish_reason == FinishReason.TOOL_USE

    def test_with_call_category(self) -> None:
        response = _stop_response()
        record = make_turn_record(
            1,
            response,
            call_category=LLMCallCategory.PRODUCTIVE,
        )
        assert record.call_category == LLMCallCategory.PRODUCTIVE

    def test_system_category(self) -> None:
        response = _stop_response()
        record = make_turn_record(
            1,
            response,
            call_category=LLMCallCategory.SYSTEM,
        )
        assert record.call_category == LLMCallCategory.SYSTEM


# ── build_result ────────────────────────────────────────────────────


@pytest.mark.unit
class TestBuildResult:
    def test_basic(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = build_result(
            sample_agent_context,
            TerminationReason.COMPLETED,
            [],
        )
        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.turns == ()
        assert result.error_message is None
        assert result.metadata == {}

    def test_with_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = build_result(
            sample_agent_context,
            TerminationReason.ERROR,
            [],
            error_message="something broke",
        )
        assert result.error_message == "something broke"

    def test_with_metadata(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = build_result(
            sample_agent_context,
            TerminationReason.COMPLETED,
            [],
            metadata={"plan": "steps"},
        )
        assert result.metadata == {"plan": "steps"}

    def test_with_turns(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        turns = [
            TurnRecord(
                turn_number=1,
                input_tokens=10,
                output_tokens=5,
                cost_usd=0.001,
                finish_reason=FinishReason.STOP,
            ),
        ]
        result = build_result(
            sample_agent_context,
            TerminationReason.COMPLETED,
            turns,
        )
        assert len(result.turns) == 1


# ── clear_last_turn_tool_calls ─────────────────────────────────────


@pytest.mark.unit
class TestClearLastTurnToolCalls:
    def test_clears_tool_calls_on_last_turn(self) -> None:
        turns = [
            TurnRecord(
                turn_number=1,
                input_tokens=10,
                output_tokens=5,
                cost_usd=0.001,
                tool_calls_made=("search", "read"),
                finish_reason=FinishReason.TOOL_USE,
            ),
        ]
        clear_last_turn_tool_calls(turns)
        assert turns[-1].tool_calls_made == ()
        # Other fields unchanged
        assert turns[-1].turn_number == 1
        assert turns[-1].finish_reason == FinishReason.TOOL_USE

    def test_empty_turns_is_noop(self) -> None:
        turns: list[TurnRecord] = []
        clear_last_turn_tool_calls(turns)
        assert turns == []

    def test_preserves_earlier_turns(self) -> None:
        turns = [
            TurnRecord(
                turn_number=1,
                input_tokens=10,
                output_tokens=5,
                cost_usd=0.001,
                tool_calls_made=("search",),
                finish_reason=FinishReason.TOOL_USE,
            ),
            TurnRecord(
                turn_number=2,
                input_tokens=20,
                output_tokens=10,
                cost_usd=0.002,
                tool_calls_made=("write",),
                finish_reason=FinishReason.TOOL_USE,
            ),
        ]
        clear_last_turn_tool_calls(turns)
        # First turn unchanged
        assert turns[0].tool_calls_made == ("search",)
        # Last turn cleared
        assert turns[1].tool_calls_made == ()
