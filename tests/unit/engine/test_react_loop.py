"""Tests for the ReAct execution loop."""

from collections.abc import Callable
from typing import TYPE_CHECKING, cast, override
from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue

from synthorg.core.agent import AgentIdentity
from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.completion_enums import FinishReason
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.loop_silent_turn import SILENT_TURN_NUDGE
from synthorg.engine.loop_unusable_turn import (
    DROPPED_CALL_NUDGE,
    MAX_CONSECUTIVE_CORRECTIONS,
    NO_CALL_NUDGE,
)
from synthorg.engine.quality.classifier import RuleBasedStepClassifier
from synthorg.engine.react_loop import ReactLoop
from synthorg.engine.resume_scope import resumed_run_scope
from synthorg.execution.turn import TurnRecord
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolCall,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry
from tests._shared import mock_of
from tests._shared.scripted_provider import ScriptedProvider

if TYPE_CHECKING:
    from .conftest import MockCompletionProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _usage(input_tokens: int = 10, output_tokens: int = 5) -> TokenUsage:
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=0.001,
    )


def _stop_response(content: str = "Done.") -> CompletionResponse:
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=_usage(),
        model="test-model-001",
    )


def _reasoning_only_response(
    reasoning: str = "weighing two layouts",
) -> CompletionResponse:
    """A turn the model spent entirely on its thinking channel."""
    return CompletionResponse(
        reasoning=reasoning,
        finish_reason=FinishReason.MAX_TOKENS,
        usage=_usage(),
        model="test-model-001",
    )


def _dropped_tool_call_response() -> CompletionResponse:
    """A turn whose only tool call was unparsable and dropped by the driver.

    The preamble is what makes this shape reachable: ``CompletionResponse``
    refuses a ``tool_use`` finish that is empty on every channel, so the case
    that survives validation is the model narrating its intent and then
    emitting argument JSON the driver could not parse.
    """
    return CompletionResponse(
        content="Let me read the file first.",
        dropped_tool_calls=True,
        finish_reason=FinishReason.TOOL_USE,
        usage=_usage(),
        model="test-model-001",
    )


def _empty_turn_response() -> CompletionResponse:
    """A turn empty on every channel, normalised to ERROR by the driver."""
    return CompletionResponse(
        content=None,
        finish_reason=FinishReason.ERROR,
        usage=_usage(),
        model="test-model-001",
    )


def _tool_use_response(
    tool_name: str = "echo",
    tool_call_id: str = "tc-1",
    arguments: dict[str, object] | None = None,
) -> CompletionResponse:
    return CompletionResponse(
        content=None,
        tool_calls=(
            ToolCall(
                id=tool_call_id,
                name=tool_name,
                arguments=cast("dict[str, JsonValue]", arguments or {}),
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
            category=ToolCategory.CODE_EXECUTION,
        )

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
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

    async def test_no_classifier_yields_no_signals(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_stop_response("All done.")])
        loop = ReactLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.quality_signals == ()

    async def test_classifier_emits_whole_run_signal(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_stop_response("All done.")])
        loop = ReactLoop(step_classifier=RuleBasedStepClassifier())

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        # ReactLoop has no plan steps, so it emits a single whole-run signal.
        assert len(result.quality_signals) == 1
        assert result.quality_signals[0].step_index == 0

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
        """With extensions disabled, the first ceiling still ends the run."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            max_turns=2,
            turn_extensions=0,
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

    async def test_the_ceiling_extends_before_it_stops(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """A run with extensions left carries on past its first ceiling.

        The work is what matters, not the estimate that sized the budget.
        """
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            max_turns=2,
            turn_extensions=1,
        )
        ctx = _ctx_with_user_msg(ctx)
        provider = mock_provider_factory(
            [_tool_use_response("echo", f"tc-{n}") for n in range(1, 4)]
        )
        invoker = _make_invoker("echo")

        result = await ReactLoop().execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        assert len(result.turns) > 2, "the run stopped at its original ceiling"
        assert result.context.turn_extensions_granted == 1


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
        assert t1.cost == 0.001
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
        # Provider receives a defensive deep copy at the engine
        # boundary: equal by value, distinct by identity.
        assert provider.recorded_configs[0] == custom_config
        assert provider.recorded_configs[0] is not custom_config


@pytest.mark.unit
class TestReactLoopProviderException:
    """Provider raising exception during complete()."""

    async def test_provider_exception_returns_error_result(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)

        loop = ReactLoop()
        result = await loop.execute(
            context=ctx,
            provider=ScriptedProvider(error=ConnectionError("connection refused")),
        )

        assert result.termination_reason == TerminationReason.ERROR
        assert result.error_message is not None
        assert "ConnectionError" in result.error_message

    async def test_provider_memory_error_propagates(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)

        loop = ReactLoop()
        with pytest.raises(MemoryError):
            await loop.execute(
                context=ctx,
                provider=ScriptedProvider(error=MemoryError()),
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
                    category=ToolCategory.CODE_EXECUTION,
                )

            @override
            async def execute(
                self,
                *,
                arguments: dict[str, object],
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

    async def test_the_correction_names_the_shape_it_saw(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """A model told the wrong thing repeats the same mistake.

        The dropped-call wording asks it to fix arguments; a turn that carried
        no call at all has none to fix, and a live run spent all three of its
        corrections telling a model its JSON was invalid when the provider had
        sent nothing, getting the identical reply each time.
        """
        ctx = _ctx_with_user_msg(sample_agent_context)
        dropped = CompletionResponse(
            content="I want to use tools",
            tool_calls=(),
            dropped_tool_calls=True,
            finish_reason=FinishReason.TOOL_USE,
            usage=_usage(),
            model="test-model-001",
        )
        provider = mock_provider_factory([dropped, _stop_response("Done.")])

        result = await ReactLoop().execute(context=ctx, provider=provider)

        corrections = [
            m.content
            for m in result.context.conversation
            if m.role == MessageRole.USER
            and m.content
            in {
                DROPPED_CALL_NUDGE,
                NO_CALL_NUDGE,
            }
        ]
        assert corrections == [DROPPED_CALL_NUDGE]

    async def test_tool_use_empty_calls_costs_its_turn_not_the_run(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """The model asked for a tool and sent none.

        One turn of the model's own bad output, so the run gets its next turn
        with a correction rather than ending on it.
        """
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = CompletionResponse(
            content="I want to use tools",
            tool_calls=(),
            finish_reason=FinishReason.TOOL_USE,
            usage=_usage(),
            model="test-model-001",
        )
        provider = mock_provider_factory([response, _stop_response("Done.")])
        loop = ReactLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        corrections = [
            m
            for m in result.context.conversation
            if m.role == MessageRole.USER and m.content == NO_CALL_NUDGE
        ]
        assert len(corrections) == 1

    async def test_the_correction_budget_bounds_the_run(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """The correction bounds itself, so a broken provider still fails."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = CompletionResponse(
            content="I want to use tools",
            tool_calls=(),
            finish_reason=FinishReason.TOOL_USE,
            usage=_usage(),
            model="test-model-001",
        )
        provider = mock_provider_factory([response] * (MAX_CONSECUTIVE_CORRECTIONS + 1))
        loop = ReactLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.ERROR
        assert result.error_message is not None
        assert "no usable output" in result.error_message


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

        loop = ReactLoop()
        with pytest.raises(RecursionError):
            await loop.execute(
                context=ctx,
                provider=ScriptedProvider(error=RecursionError()),
            )

    async def test_tool_invoke_all_recursion_error_propagates(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_tool_use_response("echo", "tc-1")])
        mock_invoker = mock_of[ToolInvoker](
            invoke_all=AsyncMock(side_effect=RecursionError)
        )
        mock_invoker.registry.to_definitions.return_value = ()
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
        mock_invoker = mock_of[ToolInvoker](
            invoke_all=AsyncMock(side_effect=MemoryError)
        )
        mock_invoker.registry.to_definitions.return_value = ()
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
        mock_invoker = mock_of[ToolInvoker](
            invoke_all=AsyncMock(side_effect=RuntimeError("TaskGroup crashed"))
        )
        mock_invoker.registry.to_definitions.return_value = ()
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
class TestReactLoopShutdown:
    """Shutdown checker triggers loop termination."""

    async def test_shutdown_before_first_turn(
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
            shutdown_checker=lambda: True,  # shutdown immediately
        )

        assert result.termination_reason == TerminationReason.SHUTDOWN
        assert len(result.turns) == 0
        assert provider.call_count == 0

    async def test_shutdown_after_first_turn(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        call_count = 0

        def shutdown_check() -> bool:
            nonlocal call_count
            call_count += 1
            # Shutdown on third check (after first turn: check at top,
            # check before tools, check at top of next iteration)
            return call_count > 2

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
            shutdown_checker=shutdown_check,
        )

        assert result.termination_reason == TerminationReason.SHUTDOWN
        assert len(result.turns) == 1

    async def test_shutdown_before_tool_execution(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Shutdown detected between LLM response and tool invocation."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        call_count = 0

        def shutdown_check() -> bool:
            nonlocal call_count
            call_count += 1
            # First check (top of loop) passes, second check (before
            # tools) triggers shutdown
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
            shutdown_checker=shutdown_check,
        )

        assert result.termination_reason == TerminationReason.SHUTDOWN
        # Turn was recorded (LLM was called), but tools were not executed
        assert len(result.turns) == 1

    async def test_no_shutdown_checker_runs_normally(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_stop_response("Done.")])
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            shutdown_checker=None,
        )

        assert result.termination_reason == TerminationReason.COMPLETED

    async def test_shutdown_checker_exception_returns_error(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Shutdown checker that raises → ERROR termination."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([])
        loop = ReactLoop()

        def bad_checker() -> bool:
            msg = "checker broke"
            raise ValueError(msg)

        result = await loop.execute(
            context=ctx,
            provider=provider,
            shutdown_checker=bad_checker,
        )

        assert result.termination_reason == TerminationReason.ERROR
        assert "Shutdown checker failed" in (result.error_message or "")

    async def test_shutdown_checker_memory_error_propagates(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """MemoryError from shutdown checker propagates unconditionally."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([])
        loop = ReactLoop()

        def oom_checker() -> bool:
            raise MemoryError

        with pytest.raises(MemoryError):
            await loop.execute(
                context=ctx,
                provider=provider,
                shutdown_checker=oom_checker,
            )


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
        assert result.context.accumulated_cost.cost > ctx.accumulated_cost.cost
        assert result.context.turn_count == 1


# ---------------------------------------------------------------------------
# Stagnation detector integration
# ---------------------------------------------------------------------------


class _FakeStagnationDetector:
    """Fake stagnation detector returning pre-configured results."""

    def __init__(
        self,
        results: list[object],
    ) -> None:
        from synthorg.engine.stagnation.models import (
            NO_STAGNATION_RESULT,
        )

        self._results = list(results)
        self._default = NO_STAGNATION_RESULT
        self.check_count = 0
        self.corrections_seen: list[int] = []

    def get_detector_type(self) -> str:
        return "fake"

    async def check(
        self,
        turns: tuple[TurnRecord, ...],
        *,
        corrections_injected: int = 0,
    ) -> object:
        self.check_count += 1
        self.corrections_seen.append(corrections_injected)
        if self._results:
            return self._results.pop(0)
        return self._default


@pytest.mark.unit
class TestReactLoopStagnationDetector:
    """Stagnation detector integration with ReactLoop."""

    async def test_no_detector_runs_normally(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_stop_response("Done.")])
        loop = ReactLoop(stagnation_detector=None)

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED

    async def test_inject_prompt_appends_user_message(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        from synthorg.engine.stagnation.models import (
            StagnationResult,
            StagnationVerdict,
        )

        inject_result = StagnationResult(
            verdict=StagnationVerdict.INJECT_PROMPT,
            corrective_message="Try something else.",
            repetition_ratio=0.8,
        )
        detector = _FakeStagnationDetector([inject_result])

        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _tool_use_response("echo", "tc-1"),
                _stop_response("Done after correction."),
            ]
        )
        invoker = _make_invoker("echo")
        loop = ReactLoop(stagnation_detector=detector)  # type: ignore[arg-type]

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        # Corrective message was injected as USER message
        user_msgs = [
            m for m in result.context.conversation if m.role == MessageRole.USER
        ]
        assert any("Try something else." in (m.content or "") for m in user_msgs)

    async def test_terminate_returns_stagnation_result(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        from synthorg.engine.stagnation.models import (
            StagnationResult,
            StagnationVerdict,
        )

        terminate_result = StagnationResult(
            verdict=StagnationVerdict.TERMINATE,
            repetition_ratio=0.9,
        )
        detector = _FakeStagnationDetector([terminate_result])

        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _tool_use_response("echo", "tc-1"),
            ]
        )
        invoker = _make_invoker("echo")
        loop = ReactLoop(stagnation_detector=detector)  # type: ignore[arg-type]

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        assert result.termination_reason == TerminationReason.STAGNATION
        assert "stagnation" in result.metadata

    async def test_corrections_counter_increments(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        from synthorg.engine.stagnation.models import (
            StagnationResult,
            StagnationVerdict,
        )

        inject1 = StagnationResult(
            verdict=StagnationVerdict.INJECT_PROMPT,
            corrective_message="Correction 1.",
            repetition_ratio=0.7,
        )
        terminate = StagnationResult(
            verdict=StagnationVerdict.TERMINATE,
            repetition_ratio=0.9,
        )
        detector = _FakeStagnationDetector([inject1, terminate])

        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _tool_use_response("echo", "tc-1"),
                _tool_use_response("echo", "tc-2"),
            ]
        )
        invoker = _make_invoker("echo")
        loop = ReactLoop(stagnation_detector=detector)  # type: ignore[arg-type]

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        assert result.termination_reason == TerminationReason.STAGNATION
        assert detector.check_count == 2
        assert detector.corrections_seen == [0, 1]


@pytest.mark.unit
class TestReactLoopNoOpFailLoud:
    """A work task that finishes without producing any artifact fails loud."""

    @staticmethod
    def _work_context(
        agent: AgentIdentity,
        task: Task,
    ) -> AgentContext:
        work_task = task.model_copy(
            update={
                "artifacts_expected": (
                    ExpectedArtifact(type=ArtifactType.CODE, path="src/x.py"),
                ),
            }
        )
        ctx = AgentContext.from_identity(agent, task=work_task)
        return _ctx_with_user_msg(ctx)

    async def test_zero_tool_work_run_is_no_op(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Two empty turns: nudged once, then failed loud."""
        ctx = self._work_context(
            sample_agent_with_personality, sample_task_with_criteria
        )
        provider = mock_provider_factory(
            [
                _stop_response("All done, trust me."),
                _stop_response("Still done, still trust me."),
            ]
        )
        loop = ReactLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.NO_OP
        assert result.total_tool_calls == 0
        assert result.error_message is not None

    async def test_a_first_empty_turn_is_corrected_not_failed(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """An agent that answered in prose gets one chance to deliver.

        Without it the zero-artifact guard fires on turn 1 and the task is
        failed with its whole budget unused.
        """
        ctx = self._work_context(
            sample_agent_with_personality, sample_task_with_criteria
        )
        provider = mock_provider_factory(
            [
                _stop_response("I would start by designing the module."),
                _tool_use_response("echo", "tc-1"),
                _stop_response("Written."),
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
        assert result.total_tool_calls == 1
        nudges = [
            m
            for m in result.context.conversation
            if m.role == MessageRole.USER
            and "Prose is not a deliverable" in (m.content or "")
        ]
        assert len(nudges) == 1, "the correction fires exactly once"
        assert "src/x.py" in (nudges[0].content or ""), (
            "the correction names the declared deliverable"
        )

    async def test_a_discovery_call_does_not_count_as_delivering(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Asking what tools exist and then answering in prose is a no-op.

        The discovery tools describe the other tools and return nothing else,
        so a run that calls one and stops has produced exactly as little as one
        that called nothing. Counting it as a tool call disarmed both guards at
        once: a recorded run asked ``list_tools``, said it would begin, and
        finished ``completed`` holding an empty workspace.
        """
        ctx = self._work_context(
            sample_agent_with_personality, sample_task_with_criteria
        )
        provider = mock_provider_factory(
            [
                _tool_use_response("list_tools", "tc-1"),
                _stop_response("Let me start by exploring the working directory."),
                _stop_response("Still exploring."),
            ]
        )
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx, provider=provider, tool_invoker=_make_invoker("list_tools")
        )

        assert result.termination_reason == TerminationReason.NO_OP
        nudges = [
            m
            for m in result.context.conversation
            if m.role == MessageRole.USER
            and "Prose is not a deliverable" in (m.content or "")
        ]
        assert len(nudges) == 1, "the correction still fires, on the first empty turn"

    async def test_a_delivering_call_after_discovery_completes(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Discovery followed by real work is a completed run, not a no-op."""
        ctx = self._work_context(
            sample_agent_with_personality, sample_task_with_criteria
        )
        provider = mock_provider_factory(
            [
                _tool_use_response("list_tools", "tc-1"),
                _tool_use_response("echo", "tc-2"),
                _stop_response("Written."),
            ]
        )
        invoker = _make_invoker("list_tools", "echo")
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx, provider=provider, tool_invoker=invoker
        )

        assert result.termination_reason == TerminationReason.COMPLETED

    async def test_no_correction_when_no_turn_remains(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """A one-turn budget has nowhere to correct, so it fails loud."""
        work_task = sample_task_with_criteria.model_copy(
            update={
                "artifacts_expected": (
                    ExpectedArtifact(type=ArtifactType.CODE, path="src/x.py"),
                ),
            }
        )
        ctx = _ctx_with_user_msg(
            AgentContext.from_identity(
                sample_agent_with_personality,
                task=work_task,
                max_turns=1,
            )
        )
        provider = mock_provider_factory([_stop_response("All done, trust me.")])
        loop = ReactLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.NO_OP

    async def test_no_correction_for_a_task_expecting_no_artifacts(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Nothing was declared, so there is nothing to correct against."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_stop_response("Here is the answer.")])
        loop = ReactLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        assert not any(
            "Prose is not a deliverable" in (m.content or "")
            for m in result.context.conversation
        )

    async def test_a_silent_reasoning_turn_is_corrected_not_fatal(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """A turn that produced only reasoning gets the run's next turn.

        The model spends the turn's whole token budget thinking, the visible
        channel comes back empty, and ending there discards every productive
        turn before it.
        """
        ctx = self._work_context(
            sample_agent_with_personality, sample_task_with_criteria
        )
        provider = mock_provider_factory(
            [
                _reasoning_only_response(),
                _tool_use_response("echo", "tc-1"),
                _stop_response("Written."),
            ]
        )
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=_make_invoker("echo"),
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.total_tool_calls == 1
        corrections = [
            m
            for m in result.context.conversation
            if m.role == MessageRole.USER and m.content == SILENT_TURN_NUDGE
        ]
        assert len(corrections) == 1

    async def test_a_dropped_tool_call_is_corrected_not_fatal(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """A malformed tool call costs its turn, not the whole run.

        The model asked for a tool and emitted argument JSON the driver could
        not parse, so the call was dropped and the completion says ``tool_use``
        with nothing in it. Ending there discards every productive turn before
        it over one turn of the model's own bad output.
        """
        ctx = self._work_context(
            sample_agent_with_personality, sample_task_with_criteria
        )
        provider = mock_provider_factory(
            [
                _dropped_tool_call_response(),
                _tool_use_response("echo", "tc-1"),
                _stop_response("Written."),
            ]
        )
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=_make_invoker("echo"),
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.total_tool_calls == 1
        corrections = [
            m
            for m in result.context.conversation
            if m.role == MessageRole.USER and m.content == DROPPED_CALL_NUDGE
        ]
        assert len(corrections) == 1

    async def test_an_empty_turn_is_corrected_not_reported_as_a_provider_error(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """A turn empty on every channel is the model's, not the provider's.

        The driver normalises it to ERROR so the loop receives something
        well-formed to recover from; treating that as a provider failure ends
        the run on a turn the model simply wasted.
        """
        ctx = self._work_context(
            sample_agent_with_personality, sample_task_with_criteria
        )
        provider = mock_provider_factory(
            [
                _empty_turn_response(),
                _tool_use_response("echo", "tc-1"),
                _stop_response("Written."),
            ]
        )
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=_make_invoker("echo"),
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.total_tool_calls == 1

    @pytest.mark.parametrize(
        "wordless",
        [_reasoning_only_response, _empty_turn_response],
        ids=["reasoning_only", "empty_on_every_channel"],
    )
    async def test_a_wordless_turn_leaves_nothing_unsendable_in_the_history(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
        wordless: Callable[[], CompletionResponse],
    ) -> None:
        """The correction re-sends the history, so the history must be sendable.

        An OpenAI-compatible provider rejects an assistant message carrying
        neither content nor tool calls outright, and the rejection lands on the
        turn AFTER the wordless one. Recording the wasted turn as an empty
        assistant message therefore turns each of these two recoveries into the
        thing that kills the run.
        """
        ctx = self._work_context(
            sample_agent_with_personality, sample_task_with_criteria
        )
        provider = mock_provider_factory(
            [wordless(), _tool_use_response("echo", "tc-1"), _stop_response("Done.")]
        )
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=_make_invoker("echo"),
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        unsendable = [
            m
            for m in result.context.conversation
            if m.role == MessageRole.ASSISTANT and not m.content and not m.tool_calls
        ]
        assert unsendable == []

    async def test_a_stumble_inside_the_budget_still_recovers(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Two bad turns in a row is a stumble, not a dead provider."""
        ctx = self._work_context(
            sample_agent_with_personality, sample_task_with_criteria
        )
        provider = mock_provider_factory(
            [
                _dropped_tool_call_response(),
                _dropped_tool_call_response(),
                _tool_use_response("echo", "tc-1"),
                _stop_response("Written."),
            ]
        )
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=_make_invoker("echo"),
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.total_tool_calls == 1

    async def test_a_productive_turn_resets_the_correction_budget(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """The bound is on a stuck model, not on a run's total stumbles.

        Enough bad turns to exhaust the budget twice over, with real work
        between them. Counted across the whole run they would end it; counted
        as the consecutive run they are, each group is inside the bound and the
        run finishes.
        """
        ctx = self._work_context(
            sample_agent_with_personality, sample_task_with_criteria
        )
        provider = mock_provider_factory(
            [
                *[_dropped_tool_call_response()] * MAX_CONSECUTIVE_CORRECTIONS,
                _tool_use_response("echo", "tc-1"),
                *[_dropped_tool_call_response()] * MAX_CONSECUTIVE_CORRECTIONS,
                _tool_use_response("echo", "tc-2"),
                _stop_response("Written."),
            ]
        )
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=_make_invoker("echo"),
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.total_tool_calls == 2

    async def test_the_correction_budget_is_bounded(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """A provider returning nothing usable still ends the run."""
        ctx = self._work_context(
            sample_agent_with_personality, sample_task_with_criteria
        )
        provider = mock_provider_factory(
            [_dropped_tool_call_response()] * (MAX_CONSECUTIVE_CORRECTIONS + 1)
        )
        loop = ReactLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.ERROR
        corrections = [
            m
            for m in result.context.conversation
            if m.role == MessageRole.USER and m.content == DROPPED_CALL_NUDGE
        ]
        assert len(corrections) == MAX_CONSECUTIVE_CORRECTIONS

    async def test_running_out_of_corrections_fails_rather_than_reports_success(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Exhausting the corrections ends the run as an error, not a success.

        The ordinary completion path sits directly after the correction, so a
        run that delivered nothing would otherwise be reported as having
        finished its work.
        """
        ctx = self._work_context(
            sample_agent_with_personality, sample_task_with_criteria
        )
        provider = mock_provider_factory(
            [_empty_turn_response()] * (MAX_CONSECUTIVE_CORRECTIONS + 1)
        )
        loop = ReactLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.ERROR
        assert "no usable output" in (result.error_message or "")

    async def test_a_provider_error_that_says_why_stays_fatal(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """A real provider failure reports its cause and must not be retried."""
        ctx = self._work_context(
            sample_agent_with_personality, sample_task_with_criteria
        )
        provider = mock_provider_factory(
            [
                CompletionResponse(
                    content="upstream refused the request",
                    finish_reason=FinishReason.ERROR,
                    usage=_usage(),
                    model="test-model-001",
                )
            ]
        )
        loop = ReactLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.ERROR
        assert "upstream refused the request" in (result.error_message or "")

    async def test_two_silent_turns_in_a_row_stop_correcting(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """The correction is its own bound, so a mute model cannot loop."""
        ctx = self._work_context(
            sample_agent_with_personality, sample_task_with_criteria
        )
        provider = mock_provider_factory(
            [_reasoning_only_response(), _reasoning_only_response()]
        )
        loop = ReactLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.NO_OP
        corrections = [
            m
            for m in result.context.conversation
            if m.role == MessageRole.USER and m.content == SILENT_TURN_NUDGE
        ]
        assert len(corrections) == 1

    async def test_work_run_with_tool_call_completes(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = self._work_context(
            sample_agent_with_personality, sample_task_with_criteria
        )
        provider = mock_provider_factory(
            [_tool_use_response("echo", "tc-1"), _stop_response("Done.")]
        )
        invoker = _make_invoker("echo")
        loop = ReactLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.total_tool_calls == 1

    async def test_chat_action_without_task_execution_still_completes(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        # A task that expects no deliverable (empty ``artifacts_expected``)
        # legitimately answers in text and must NOT be reclassified NO_OP.
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_stop_response("Here is the answer.")])
        loop = ReactLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED

    async def test_resumed_zero_tool_work_run_completes(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """A resumed run's empty segment is not NO_OP.

        Inside a ``resumed_run_scope`` the current segment's zero-tool-call
        count is not a valid proxy for total task output (earlier segments
        may already have produced artifacts before a park), so the empty
        segment completes to review rather than being failed as NO_OP.
        """
        ctx = self._work_context(
            sample_agent_with_personality, sample_task_with_criteria
        )
        provider = mock_provider_factory([_stop_response("Resuming; already done.")])
        loop = ReactLoop()

        with resumed_run_scope():
            result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.total_tool_calls == 0
