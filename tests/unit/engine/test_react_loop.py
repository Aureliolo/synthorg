"""Tests for the ReAct execution loop."""

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_company.core.agent import AgentIdentity  # noqa: TC001
from ai_company.engine.context import AgentContext
from ai_company.engine.loop_protocol import TerminationReason
from ai_company.engine.react_loop import ReactLoop
from ai_company.providers.enums import FinishReason, MessageRole
from ai_company.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolCall,
)
from ai_company.tools.base import BaseTool, ToolExecutionResult
from ai_company.tools.invoker import ToolInvoker
from ai_company.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from .conftest import MockCompletionProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _usage(input_tokens: int = 10, output_tokens: int = 5) -> TokenUsage:
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
    arguments: dict[str, Any] | None = None,
) -> CompletionResponse:
    return CompletionResponse(
        content=None,
        tool_calls=(
            ToolCall(
                id=tool_call_id,
                name=tool_name,
                arguments=arguments or {},
            ),
        ),
        finish_reason=FinishReason.TOOL_USE,
        usage=_usage(),
        model="test-model-001",
    )


def _content_filter_response() -> CompletionResponse:
    return CompletionResponse(
        content=None,
        finish_reason=FinishReason.CONTENT_FILTER,
        usage=_usage(),
        model="test-model-001",
    )


def _error_response() -> CompletionResponse:
    return CompletionResponse(
        content=None,
        finish_reason=FinishReason.ERROR,
        usage=_usage(),
        model="test-model-001",
    )


class _StubTool(BaseTool):
    """Minimal tool for testing."""

    def __init__(self, name: str = "echo") -> None:
        super().__init__(
            name=name,
            description="Test echo tool",
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
    """Add a user message so the conversation is non-empty."""
    msg = ChatMessage(role=MessageRole.USER, content="Do something")
    return ctx.with_message(msg)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReactLoopBasicCompletion:
    """LLM returns STOP on turn 1, no tools."""

    async def test_single_turn_completion(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_stop_response("All done.")])
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert len(result.turns) == 1
        assert result.total_tool_calls == 0
        assert result.error_message is None
        assert result.turns[0].turn_number == 1
        assert result.turns[0].finish_reason == FinishReason.STOP
        assert result.turns[0].tool_calls_made == ()

    async def test_context_has_assistant_message(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_stop_response("Hello!")])
        loop = ReactLoop()

        result = await loop.execute(context=ctx, provider=provider)

        # Conversation should have: user msg + assistant msg
        assert len(result.context.conversation) == 2
        last_msg = result.context.conversation[-1]
        assert last_msg.role == MessageRole.ASSISTANT
        assert last_msg.content == "Hello!"


@pytest.mark.unit
class TestReactLoopToolCalls:
    """LLM requests tools, then completes."""

    async def test_single_tool_call_then_complete(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _tool_use_response("echo", "tc-1"),
                _stop_response("Done after tool."),
            ]
        )
        invoker = _make_invoker("echo")
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert len(result.turns) == 2
        assert result.total_tool_calls == 1
        assert result.turns[0].tool_calls_made == ("echo",)
        assert result.turns[0].finish_reason == FinishReason.TOOL_USE
        assert result.turns[1].finish_reason == FinishReason.STOP

    async def test_multi_turn_tool_calls(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _tool_use_response("echo", "tc-1"),
                _tool_use_response("echo", "tc-2"),
                _tool_use_response("echo", "tc-3"),
                _stop_response("Finally done."),
            ]
        )
        invoker = _make_invoker("echo")
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert len(result.turns) == 4
        assert result.total_tool_calls == 3

    async def test_tool_results_in_conversation(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _tool_use_response("echo", "tc-1"),
                _stop_response("Done."),
            ]
        )
        invoker = _make_invoker("echo")
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        # Conversation: user, assistant(tool_use), tool(result), assistant(stop)
        msgs = result.context.conversation
        assert len(msgs) == 4
        assert msgs[0].role == MessageRole.USER
        assert msgs[1].role == MessageRole.ASSISTANT
        assert msgs[2].role == MessageRole.TOOL
        assert msgs[2].tool_result is not None
        assert msgs[2].tool_result.tool_call_id == "tc-1"
        assert msgs[3].role == MessageRole.ASSISTANT


@pytest.mark.unit
class TestReactLoopMaxTurns:
    """Loop exhausts turn limit."""

    async def test_max_turns_termination(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            max_turns=2,
        )
        ctx = _ctx_with_user_msg(ctx)
        # Both turns request tools, never stops
        provider = mock_provider_factory(
            [
                _tool_use_response("echo", "tc-1"),
                _tool_use_response("echo", "tc-2"),
            ]
        )
        invoker = _make_invoker("echo")
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        assert result.termination_reason == TerminationReason.MAX_TURNS
        assert len(result.turns) == 2
        assert result.context.turn_count == 2


@pytest.mark.unit
class TestReactLoopBudgetExhausted:
    """Budget checker triggers termination."""

    async def test_budget_exhausted_before_first_turn(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([])
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            budget_checker=lambda _: True,  # always exhausted
        )

        assert result.termination_reason == TerminationReason.BUDGET_EXHAUSTED
        assert len(result.turns) == 0
        assert result.total_tool_calls == 0
        assert provider.call_count == 0

    async def test_budget_exhausted_after_first_turn(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        call_count = 0

        def budget_check(_ctx: AgentContext) -> bool:
            nonlocal call_count
            call_count += 1
            # Exhausted on second check (after first turn)
            return call_count > 1

        provider = mock_provider_factory(
            [
                _tool_use_response("echo", "tc-1"),
            ]
        )
        invoker = _make_invoker("echo")
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
            budget_checker=budget_check,
        )

        assert result.termination_reason == TerminationReason.BUDGET_EXHAUSTED
        assert len(result.turns) == 1


@pytest.mark.unit
class TestReactLoopNoToolInvoker:
    """LLM requests tools but no invoker available."""

    async def test_error_when_tools_requested_without_invoker(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _tool_use_response("echo", "tc-1"),
            ]
        )
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=None,
        )

        assert result.termination_reason == TerminationReason.ERROR
        assert result.error_message is not None
        assert "no tool invoker" in result.error_message


@pytest.mark.unit
class TestReactLoopErrorResponses:
    """LLM returns error or content_filter finish reason."""

    async def test_content_filter_terminates_with_error(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_content_filter_response()])
        loop = ReactLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.ERROR
        assert result.error_message is not None
        assert "content_filter" in result.error_message

    async def test_error_finish_reason_terminates(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_error_response()])
        loop = ReactLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.ERROR
        assert result.error_message is not None
        assert "error" in result.error_message


@pytest.mark.unit
class TestReactLoopTurnRecords:
    """Verify per-turn metadata accuracy."""

    async def test_turn_record_accuracy(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _tool_use_response("echo", "tc-1"),
                _stop_response("Done."),
            ]
        )
        invoker = _make_invoker("echo")
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        assert len(result.turns) == 2

        t1 = result.turns[0]
        assert t1.turn_number == 1
        assert t1.input_tokens == 10
        assert t1.output_tokens == 5
        assert t1.cost_usd == 0.001
        assert t1.tool_calls_made == ("echo",)
        assert t1.finish_reason == FinishReason.TOOL_USE

        t2 = result.turns[1]
        assert t2.turn_number == 2
        assert t2.tool_calls_made == ()
        assert t2.finish_reason == FinishReason.STOP

    async def test_total_tool_calls_accumulated(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _tool_use_response("echo", "tc-1"),
                _tool_use_response("echo", "tc-2"),
                _stop_response("Done."),
            ]
        )
        invoker = _make_invoker("echo")
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        assert result.total_tool_calls == 2


@pytest.mark.unit
class TestReactLoopContextImmutability:
    """Original context unchanged after execution."""

    async def test_original_context_unchanged(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        original_turn_count = ctx.turn_count
        original_conv_len = len(ctx.conversation)
        original_cost = ctx.accumulated_cost

        provider = mock_provider_factory(
            [
                _tool_use_response("echo", "tc-1"),
                _stop_response("Done."),
            ]
        )
        invoker = _make_invoker("echo")
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        # Original unchanged
        assert ctx.turn_count == original_turn_count
        assert len(ctx.conversation) == original_conv_len
        assert ctx.accumulated_cost == original_cost

        # Result has evolved state
        assert result.context.turn_count > original_turn_count
        assert len(result.context.conversation) > original_conv_len


@pytest.mark.unit
class TestReactLoopConversationState:
    """Final context has all messages."""

    async def test_full_conversation_preserved(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _tool_use_response("echo", "tc-1"),
                _stop_response("Final answer."),
            ]
        )
        invoker = _make_invoker("echo")
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        roles = [m.role for m in result.context.conversation]
        assert roles == [
            MessageRole.USER,
            MessageRole.ASSISTANT,  # tool_use turn
            MessageRole.TOOL,  # tool result
            MessageRole.ASSISTANT,  # final response
        ]


@pytest.mark.unit
class TestReactLoopCompletionConfig:
    """Per-execution completion config override."""

    async def test_custom_completion_config(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_stop_response("Ok.")])
        loop = ReactLoop()
        custom_config = CompletionConfig(temperature=0.1, max_tokens=100)

        result = await loop.execute(
            context=ctx,
            provider=provider,
            completion_config=custom_config,
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert len(provider.recorded_configs) == 1
        assert provider.recorded_configs[0] is custom_config


@pytest.mark.unit
class TestReactLoopProviderException:
    """Provider raising exception during complete()."""

    async def test_provider_exception_returns_error_result(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)

        class _FailingProvider:
            async def complete(self, *_args: Any, **_kwargs: Any) -> None:
                msg = "connection refused"
                raise ConnectionError(msg)

        loop = ReactLoop()
        result = await loop.execute(
            context=ctx,
            provider=_FailingProvider(),  # type: ignore[arg-type]
        )

        assert result.termination_reason == TerminationReason.ERROR
        assert result.error_message is not None
        assert "ConnectionError" in result.error_message

    async def test_provider_memory_error_propagates(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)

        class _OOMProvider:
            async def complete(self, *_args: Any, **_kwargs: Any) -> None:
                raise MemoryError

        loop = ReactLoop()
        with pytest.raises(MemoryError):
            await loop.execute(
                context=ctx,
                provider=_OOMProvider(),  # type: ignore[arg-type]
            )


@pytest.mark.unit
class TestReactLoopToolExecutionException:
    """Tool execution errors are captured by ToolInvoker and do not crash the loop."""

    async def test_tool_exception_returns_error_result(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_tool_use_response("explode", "tc-1")])

        class _ExplodingTool(BaseTool):
            def __init__(self) -> None:
                super().__init__(
                    name="explode",
                    description="boom",
                )

            async def execute(
                self,
                *,
                arguments: dict[str, Any],
            ) -> ToolExecutionResult:
                msg = "kaboom"
                raise RuntimeError(msg)

        registry = ToolRegistry([_ExplodingTool()])
        invoker = ToolInvoker(registry)
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        # The tool error is caught by ToolInvoker.invoke and returned
        # as ToolResult(is_error=True), so the loop continues normally.
        # It terminates with ERROR because the mock has no more
        # responses, causing an IndexError in the next provider call.
        assert result.termination_reason == TerminationReason.ERROR


@pytest.mark.unit
class TestReactLoopMaxTokensFinishReason:
    """MAX_TOKENS finish reason with no tool calls."""

    async def test_max_tokens_returns_completed(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = CompletionResponse(
            content="partial output",
            finish_reason=FinishReason.MAX_TOKENS,
            usage=_usage(),
            model="test-model-001",
        )
        provider = mock_provider_factory([response])
        loop = ReactLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        assert len(result.turns) == 1
        assert result.turns[0].finish_reason == FinishReason.MAX_TOKENS


@pytest.mark.unit
class TestReactLoopToolUseEmptyToolCalls:
    """TOOL_USE finish reason with no actual tool calls."""

    async def test_tool_use_empty_calls_returns_error(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = CompletionResponse(
            content="I want to use tools",
            tool_calls=(),
            finish_reason=FinishReason.TOOL_USE,
            usage=_usage(),
            model="test-model-001",
        )
        provider = mock_provider_factory([response])
        loop = ReactLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.ERROR
        assert result.error_message is not None
        assert "TOOL_USE" in result.error_message


@pytest.mark.unit
class TestReactLoopBudgetCheckerException:
    """Budget checker callback raising an exception."""

    async def test_budget_checker_exception_returns_error(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([])
        loop = ReactLoop()

        def bad_checker(_ctx: AgentContext) -> bool:
            msg = "db connection lost"
            raise ConnectionError(msg)

        result = await loop.execute(
            context=ctx,
            provider=provider,
            budget_checker=bad_checker,
        )

        assert result.termination_reason == TerminationReason.ERROR
        assert result.error_message is not None
        assert "Budget checker failed" in result.error_message


@pytest.mark.unit
class TestReactLoopRecursionErrorPropagation:
    """RecursionError propagates from provider and tool execution."""

    async def test_provider_recursion_error_propagates(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)

        class _RecursionProvider:
            async def complete(self, *_args: Any, **_kwargs: Any) -> None:
                raise RecursionError

        loop = ReactLoop()
        with pytest.raises(RecursionError):
            await loop.execute(
                context=ctx,
                provider=_RecursionProvider(),  # type: ignore[arg-type]
            )

    async def test_tool_invoke_all_recursion_error_propagates(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_tool_use_response("echo", "tc-1")])
        mock_invoker = MagicMock()
        mock_invoker.registry.to_definitions.return_value = ()
        mock_invoker.invoke_all = AsyncMock(side_effect=RecursionError)
        loop = ReactLoop()

        with pytest.raises(RecursionError):
            await loop.execute(
                context=ctx,
                provider=provider,
                tool_invoker=mock_invoker,
            )

    async def test_tool_invoke_all_memory_error_propagates(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_tool_use_response("echo", "tc-1")])
        mock_invoker = MagicMock()
        mock_invoker.registry.to_definitions.return_value = ()
        mock_invoker.invoke_all = AsyncMock(side_effect=MemoryError)
        loop = ReactLoop()

        with pytest.raises(MemoryError):
            await loop.execute(
                context=ctx,
                provider=provider,
                tool_invoker=mock_invoker,
            )


@pytest.mark.unit
class TestReactLoopInvokeAllException:
    """invoke_all raising an exception is caught and returned as error."""

    async def test_invoke_all_exception_returns_error_result(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_tool_use_response("echo", "tc-1")])
        mock_invoker = MagicMock()
        mock_invoker.registry.to_definitions.return_value = ()
        mock_invoker.invoke_all = AsyncMock(
            side_effect=RuntimeError("TaskGroup crashed"),
        )
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=mock_invoker,
        )

        assert result.termination_reason == TerminationReason.ERROR
        assert result.error_message is not None
        assert "Tool execution failed" in result.error_message
        assert "RuntimeError" in result.error_message


@pytest.mark.unit
class TestReactLoopEmptyToolRegistry:
    """Empty ToolRegistry causes tool_defs to be None."""

    async def test_empty_registry_passes_no_tools(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_stop_response("Done.")])
        registry = ToolRegistry([])
        invoker = ToolInvoker(registry)
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        assert result.termination_reason == TerminationReason.COMPLETED


@pytest.mark.unit
class TestReactLoopCostAccounting:
    """Error responses include the failing turn's cost in context."""

    async def test_content_filter_response_cost_in_context(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_content_filter_response()])
        loop = ReactLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.ERROR
        # The failing turn's cost should be in the context
        assert result.context.accumulated_cost.cost_usd > ctx.accumulated_cost.cost_usd
        assert result.context.turn_count == 1
