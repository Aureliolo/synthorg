"""Tests for tool-call feedback emission in ``BaseCompletionProvider``.

The provider boundary observes tool-call outcomes for tools-bearing
requests: a response carrying tool calls is a SUCCESS (proof), a
non-retryable ``InvalidRequestError`` or a malformed ``TOOL_USE``
response is a FAILURE, and transient errors / tool-free requests emit
nothing.
"""

from collections.abc import AsyncIterator, Iterator
from typing import override

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.enums import MessageRole, StreamEventType
from synthorg.providers.errors import (
    InvalidRequestError,
    ProviderTimeoutError,
)
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)
from synthorg.providers.tool_call_feedback.sink import (
    ToolCallOutcome,
    install_tool_call_signal_sink,
    uninstall_tool_call_signal_sink,
)

pytestmark = pytest.mark.unit

_TOOLS = [ToolDefinition(name="search")]
_USAGE = TokenUsage(input_tokens=1, output_tokens=1, cost=0.0)


class _RecordingSink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, ToolCallOutcome]] = []

    async def record(
        self, *, provider: str, model: str, outcome: ToolCallOutcome
    ) -> None:
        self.calls.append((provider, model, outcome))


@pytest.fixture(autouse=True)
def _clean_sink() -> Iterator[None]:
    # Guard the process-global sink against a leak from another module whose
    # teardown failed (e.g. an xdist worker crash) so these tests never start
    # with a foreign sink installed.
    uninstall_tool_call_signal_sink()
    yield
    uninstall_tool_call_signal_sink()


@pytest.fixture
def sink() -> Iterator[_RecordingSink]:
    recording = _RecordingSink()
    install_tool_call_signal_sink(recording)
    yield recording
    uninstall_tool_call_signal_sink()


class _ConfiguredProvider(BaseCompletionProvider):
    """Provider returning a canned response (or raising) on complete."""

    def __init__(
        self,
        *,
        response: CompletionResponse | None = None,
        exc: Exception | None = None,
        stream_chunks: tuple[StreamChunk, ...] = (),
    ) -> None:
        super().__init__()
        self._provider_name = "example-provider"
        self._response = response
        self._exc = exc
        self._stream_chunks = stream_chunks

    @override
    async def _do_complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        if self._exc is not None:
            raise self._exc
        assert self._response is not None
        return self._response

    @override
    async def _do_stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        if self._exc is not None:
            raise self._exc
        chunks = self._stream_chunks

        async def _gen() -> AsyncIterator[StreamChunk]:
            for chunk in chunks:
                yield chunk

        return _gen()

    @override
    async def _do_get_model_capabilities(self, model: str) -> ModelCapabilities:
        msg = "not implemented"
        raise NotImplementedError(msg)


def _user_msg() -> list[ChatMessage]:
    return [ChatMessage(role=MessageRole.USER, content="hi")]


class TestCompleteFeedback:
    async def test_tool_calls_present_emits_success(self, sink: _RecordingSink) -> None:
        response = CompletionResponse(
            tool_calls=(ToolCall(id="1", name="search", arguments={}),),
            finish_reason=FinishReason.TOOL_USE,
            usage=_USAGE,
            model="example-large-001",
        )
        provider = _ConfiguredProvider(response=response)
        await provider.complete(_user_msg(), "example-large-001", tools=_TOOLS)
        assert sink.calls == [
            ("example-provider", "example-large-001", ToolCallOutcome.SUCCESS)
        ]

    async def test_invalid_request_emits_failure(self, sink: _RecordingSink) -> None:
        provider = _ConfiguredProvider(exc=InvalidRequestError("no tools support"))
        with pytest.raises(InvalidRequestError):
            await provider.complete(_user_msg(), "example-large-001", tools=_TOOLS)
        assert sink.calls == [
            ("example-provider", "example-large-001", ToolCallOutcome.FAILURE)
        ]

    async def test_malformed_tool_use_emits_failure(self, sink: _RecordingSink) -> None:
        response = CompletionResponse(
            content="I will search",
            tool_calls=(),
            finish_reason=FinishReason.TOOL_USE,
            usage=_USAGE,
            model="example-large-001",
        )
        provider = _ConfiguredProvider(response=response)
        await provider.complete(_user_msg(), "example-large-001", tools=_TOOLS)
        assert sink.calls == [
            ("example-provider", "example-large-001", ToolCallOutcome.FAILURE)
        ]

    async def test_retryable_error_emits_nothing(self, sink: _RecordingSink) -> None:
        provider = _ConfiguredProvider(exc=ProviderTimeoutError("slow"))
        with pytest.raises(ProviderTimeoutError):
            await provider.complete(_user_msg(), "example-large-001", tools=_TOOLS)
        assert sink.calls == []

    async def test_plain_text_answer_emits_nothing(self, sink: _RecordingSink) -> None:
        response = CompletionResponse(
            content="just text",
            tool_calls=(),
            finish_reason=FinishReason.STOP,
            usage=_USAGE,
            model="example-large-001",
        )
        provider = _ConfiguredProvider(response=response)
        await provider.complete(_user_msg(), "example-large-001", tools=_TOOLS)
        assert sink.calls == []

    async def test_no_tools_requested_emits_nothing(self, sink: _RecordingSink) -> None:
        response = CompletionResponse(
            tool_calls=(ToolCall(id="1", name="search", arguments={}),),
            finish_reason=FinishReason.TOOL_USE,
            usage=_USAGE,
            model="example-large-001",
        )
        provider = _ConfiguredProvider(response=response)
        await provider.complete(_user_msg(), "example-large-001")
        assert sink.calls == []


class TestStreamFeedback:
    async def test_stream_setup_invalid_request_emits_failure(
        self, sink: _RecordingSink
    ) -> None:
        provider = _ConfiguredProvider(exc=InvalidRequestError("no tools support"))
        with pytest.raises(InvalidRequestError):
            await provider.stream(_user_msg(), "example-large-001", tools=_TOOLS)
        assert sink.calls == [
            ("example-provider", "example-large-001", ToolCallOutcome.FAILURE)
        ]

    async def test_stream_tool_call_delta_emits_success(
        self, sink: _RecordingSink
    ) -> None:
        chunks = (
            StreamChunk(
                event_type=StreamEventType.TOOL_CALL_DELTA,
                tool_call_delta=ToolCall(id="1", name="search", arguments={}),
            ),
            StreamChunk(event_type=StreamEventType.USAGE, usage=_USAGE),
        )
        provider = _ConfiguredProvider(stream_chunks=chunks)
        iterator = await provider.stream(_user_msg(), "example-large-001", tools=_TOOLS)
        async for _ in iterator:
            pass
        assert sink.calls == [
            ("example-provider", "example-large-001", ToolCallOutcome.SUCCESS)
        ]
