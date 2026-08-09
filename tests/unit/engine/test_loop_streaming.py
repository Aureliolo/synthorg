"""Tests for the streaming provider turn and mid-turn interruption."""

from datetime import UTC, datetime

import pytest
import structlog.testing

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
from synthorg.observability.events.provider import PROVIDER_EMPTY_COMPLETION
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


def _reasoning(text: str) -> StreamChunk:
    return StreamChunk(event_type=StreamEventType.REASONING_DELTA, content=text)


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


class _DelayedRedirectInbox:
    """Returns no directive on the first poll, then a REDIRECT thereafter.

    Lets a test prove the interrupt poll recurs past the index-0 boundary:
    a directive that only becomes pending after the first poll is caught at
    the next poll (chunk index 8), not missed and not polled every chunk.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def pending(
        self,
        *,
        project_id: str,
        task_id: str | None = None,
        agent_id: str | None = None,
        already_adopted: frozenset[str] = frozenset(),
    ) -> tuple[ActiveSteeringDirective, ...]:
        del project_id, task_id, agent_id, already_adopted
        self.calls += 1
        return () if self.calls == 1 else (_redirect_directive(),)


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
        tool_defs=None,
        config=CompletionConfig(),
        turn_number=1,
        turns=turns,
        cancellation_checker=cancellation_checker or _never_cancelled,  # type: ignore[arg-type]
        steering_inbox=steering_inbox,  # type: ignore[arg-type]
    )


class TestStreamProviderReassembly:
    async def test_content_and_usage_reassembled(
        self, sample_agent_context: AgentContext
    ) -> None:
        result = await _stream(
            sample_agent_context,
            [_content("He"), _content("llo"), _usage_chunk(), _done(FinishReason.STOP)],
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

    async def test_an_empty_completion_says_it_was_empty(
        self, sample_agent_context: AgentContext
    ) -> None:
        """The streamed turn reports it, like the non-streamed one does.

        This path carried its own copy of the normalisation and none of the
        reporting, so a live task that produced an empty turn 48 failed with
        "LLM returned error on turn 48" and nothing anywhere saying the
        completion had simply come back empty.
        """
        with structlog.testing.capture_logs() as logs:
            await _stream(sample_agent_context, [_done()])

        assert any(entry["event"] == PROVIDER_EMPTY_COMPLETION for entry in logs)

    async def test_reasoning_only_turn_is_not_an_error(
        self, sample_agent_context: AgentContext
    ) -> None:
        """A turn spent entirely on the thinking channel is still a turn.

        The loop sends a reasoning effort and read only ``delta.content``, so
        a model that answered on its other channel looked like a model that
        said nothing: the turn became ``ERROR`` and the task failed, throwing
        away every turn before it.
        """
        result = await _stream(
            sample_agent_context,
            [_reasoning("weighing "), _reasoning("two layouts")],
        )
        assert isinstance(result, CompletionResponse)
        assert result.reasoning == "weighing two layouts"
        assert result.content is None
        assert result.finish_reason is not FinishReason.ERROR

    async def test_reasoning_is_kept_out_of_content(
        self, sample_agent_context: AgentContext
    ) -> None:
        # Replaying the model's working back as assistant content would change
        # what it sees next, so the two channels stay apart.
        result = await _stream(
            sample_agent_context,
            [_reasoning("thinking"), _content("answer"), _done(FinishReason.STOP)],
        )
        assert isinstance(result, CompletionResponse)
        assert result.content == "answer"
        assert result.reasoning == "thinking"

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

    async def test_stream_error_folds_partial_usage(
        self, sample_agent_context: AgentContext
    ) -> None:
        # Tokens the provider billed before signalling an ERROR chunk must be
        # folded into the ERROR result's cost (parity with the cancel path), so
        # a mid-stream failure does not under-count spend.
        before = sample_agent_context.accumulated_cost.input_tokens
        error_chunk = StreamChunk(
            event_type=StreamEventType.ERROR, error_message="boom"
        )
        result = await _stream(sample_agent_context, [_usage_chunk(), error_chunk])
        assert isinstance(result, ExecutionResult)
        assert result.termination_reason is TerminationReason.ERROR
        assert result.context.accumulated_cost.input_tokens == before + 100


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

    async def test_redirect_caught_at_next_poll_boundary_not_every_chunk(
        self, sample_agent_context: AgentContext
    ) -> None:
        # The poll runs at chunk index 0, 8, 16... A directive that becomes
        # pending only after the first poll must still be caught at the next
        # boundary (index 8): proving the poll recurs (not index-0 only) and is
        # batched (not once per chunk). Nine content chunks -> polls at 0 and 8.
        inbox = _DelayedRedirectInbox()
        result = await _stream(
            sample_agent_context,
            [_content(str(i)) for i in range(9)],
            steering_inbox=inbox,
        )
        assert isinstance(result, _TurnInterrupted)
        assert inbox.calls == 2


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
            tool_defs=None,
            config=CompletionConfig(),
            turn_number=1,
            turns=turns,
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
            tool_defs=None,
            config=CompletionConfig(),
            turn_number=1,
            turns=turns,
            streaming_enabled=True,
            cancellation_checker=_never_cancelled,
            steering_inbox=None,
        )
        assert isinstance(outcome, CompletionResponse)
        # complete() was never called on the streaming path.
        assert provider.call_count == 0
