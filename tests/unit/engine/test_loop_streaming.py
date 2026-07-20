"""Tests for the streaming provider turn and mid-turn interruption."""

from datetime import UTC, datetime

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.core.types import NotBlankStr
from synthorg.engine.context import AgentContext
from synthorg.engine.intervention.enums import InterventionKind
from synthorg.engine.intervention.models import ActiveSteeringDirective
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.loop_streaming import (
    _TurnInterrupted,
    run_provider_turn,
    stream_provider,
)
from synthorg.execution.turn import TurnRecord
from synthorg.providers.enums import StreamEventType
from synthorg.providers.models import (
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolCall,
)
from tests._shared.scripted_provider import ScriptedProvider

pytestmark = pytest.mark.unit

_MODEL = "example-medium-001"
_RECORDED_AT = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)


def _usage() -> TokenUsage:
    return TokenUsage(input_tokens=100, output_tokens=20, cost=0.01)


def _content(text: str) -> StreamChunk:
    return StreamChunk(event_type=StreamEventType.CONTENT_DELTA, content=text)


def _usage_chunk() -> StreamChunk:
    return StreamChunk(event_type=StreamEventType.USAGE, usage=_usage())


def _done(finish: FinishReason | None = None) -> StreamChunk:
    return StreamChunk(event_type=StreamEventType.DONE, finish_reason=finish)


def _tool_call_chunk() -> StreamChunk:
    return StreamChunk(
        event_type=StreamEventType.TOOL_CALL_DELTA,
        tool_call_delta=ToolCall(id="c1", name="run_tests", arguments={}),
    )


async def _never_cancelled() -> bool:
    return False


async def _always_cancelled() -> bool:
    return True


def _redirect_directive() -> ActiveSteeringDirective:
    return ActiveSteeringDirective(
        entry_id=NotBlankStr("d1"),
        kind=InterventionKind.REDIRECT,
        text=NotBlankStr("use Postgres not Mongo"),
        author=NotBlankStr("mission-control"),
        recorded_at=_RECORDED_AT,
    )


class _RedirectInbox:
    async def pending(
        self,
        *,
        project_id: str,
        task_id: str | None = None,
        agent_id: str | None = None,
        already_adopted: frozenset[str] = frozenset(),
    ) -> tuple[ActiveSteeringDirective, ...]:
        del project_id, task_id, agent_id, already_adopted
        return (_redirect_directive(),)


async def _stream(
    ctx: AgentContext,
    chunks: list[StreamChunk],
    *,
    cancellation_checker: object = None,
    steering_inbox: object = None,
) -> CompletionResponse | ExecutionResult | _TurnInterrupted:
    provider = ScriptedProvider(stream_chunks=chunks)
    turns: list[TurnRecord] = []
    return await stream_provider(
        ctx,
        provider,
        _MODEL,
        None,
        CompletionConfig(),
        1,
        turns,
        cancellation_checker=cancellation_checker or _never_cancelled,  # type: ignore[arg-type]
        steering_inbox=steering_inbox,  # type: ignore[arg-type]
    )


class TestStreamProviderReassembly:
    async def test_content_and_usage_reassembled(
        self, sample_agent_context: AgentContext
    ) -> None:
        result = await _stream(
            sample_agent_context,
            [_content("Hel"), _content("lo"), _usage_chunk(), _done(FinishReason.STOP)],
        )
        assert isinstance(result, CompletionResponse)
        assert result.content == "Hello"
        assert result.finish_reason is FinishReason.STOP
        assert result.usage.input_tokens == 100
        assert result.model == _MODEL

    async def test_tool_calls_infer_tool_use(
        self, sample_agent_context: AgentContext
    ) -> None:
        result = await _stream(
            sample_agent_context,
            [_tool_call_chunk(), _usage_chunk(), _done()],
        )
        assert isinstance(result, CompletionResponse)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "run_tests"
        # No finish reason on DONE -> inferred from the tool call.
        assert result.finish_reason is FinishReason.TOOL_USE

    async def test_empty_completion_normalised_to_error(
        self, sample_agent_context: AgentContext
    ) -> None:
        result = await _stream(sample_agent_context, [_done()])
        assert isinstance(result, CompletionResponse)
        assert result.finish_reason is FinishReason.ERROR

    async def test_done_finish_reason_preserved(
        self, sample_agent_context: AgentContext
    ) -> None:
        result = await _stream(
            sample_agent_context,
            [_content("x"), _done(FinishReason.MAX_TOKENS)],
        )
        assert isinstance(result, CompletionResponse)
        assert result.finish_reason is FinishReason.MAX_TOKENS

    async def test_stream_error_event_terminates_error(
        self, sample_agent_context: AgentContext
    ) -> None:
        error_chunk = StreamChunk(
            event_type=StreamEventType.ERROR, error_message="boom"
        )
        result = await _stream(sample_agent_context, [error_chunk])
        assert isinstance(result, ExecutionResult)
        assert result.termination_reason is TerminationReason.ERROR


class TestStreamProviderInterruption:
    async def test_hard_cancel_terminates_cancelled(
        self, sample_agent_context: AgentContext
    ) -> None:
        result = await _stream(
            sample_agent_context,
            [_content("partial")],
            cancellation_checker=_always_cancelled,
        )
        assert isinstance(result, ExecutionResult)
        assert result.termination_reason is TerminationReason.CANCELLED

    async def test_cancel_folds_partial_usage(
        self, sample_agent_context: AgentContext
    ) -> None:
        # A usage chunk arrives before the (index-0) cancel poll, so its tokens
        # are folded into the cancelled run's accumulated cost.
        before = sample_agent_context.accumulated_cost.input_tokens
        result = await _stream(
            sample_agent_context,
            [_usage_chunk()],
            cancellation_checker=_always_cancelled,
        )
        assert isinstance(result, ExecutionResult)
        assert result.termination_reason is TerminationReason.CANCELLED
        assert result.context.accumulated_cost.input_tokens == before + 100

    async def test_steer_redirect_returns_interrupt(
        self, sample_agent_context: AgentContext
    ) -> None:
        result = await _stream(
            sample_agent_context,
            [_usage_chunk()],
            steering_inbox=_RedirectInbox(),
        )
        assert isinstance(result, _TurnInterrupted)
        assert result.partial_usage.input_tokens == 100

    async def test_no_steering_inbox_completes(
        self, sample_agent_context: AgentContext
    ) -> None:
        result = await _stream(
            sample_agent_context,
            [_content("done"), _done(FinishReason.STOP)],
        )
        assert isinstance(result, CompletionResponse)


class TestRunProviderTurnDispatch:
    async def test_non_streaming_uses_complete(
        self, sample_agent_context: AgentContext
    ) -> None:
        response = CompletionResponse(
            content="hi",
            finish_reason=FinishReason.STOP,
            usage=_usage(),
            model=NotBlankStr(_MODEL),
        )
        provider = ScriptedProvider(response=response)
        turns: list[TurnRecord] = []
        outcome = await run_provider_turn(
            sample_agent_context,
            provider,
            _MODEL,
            None,
            CompletionConfig(),
            1,
            turns,
            streaming_enabled=False,
            cancellation_checker=_never_cancelled,
            steering_inbox=None,
        )
        assert isinstance(outcome, CompletionResponse)
        assert provider.call_count == 1

    async def test_streaming_uses_stream(
        self, sample_agent_context: AgentContext
    ) -> None:
        provider = ScriptedProvider(
            stream_chunks=[_content("hi"), _done(FinishReason.STOP)]
        )
        turns: list[TurnRecord] = []
        outcome = await run_provider_turn(
            sample_agent_context,
            provider,
            _MODEL,
            None,
            CompletionConfig(),
            1,
            turns,
            streaming_enabled=True,
            cancellation_checker=_never_cancelled,
            steering_inbox=None,
        )
        assert isinstance(outcome, CompletionResponse)
        # complete() was never called on the streaming path.
        assert provider.call_count == 0
