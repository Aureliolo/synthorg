"""Unit tests for the Phase-2 LLM-backed compaction summariser."""

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.engine.compaction.llm_summarizer import LLMSummarizer
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
)

pytestmark = pytest.mark.unit

_FALLBACK = "[Archived 2 messages. Summary of prior work: did stuff]"


def _response(content: str | None) -> CompletionResponse:
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=1, output_tokens=1, cost=0.0),
        model="example-small-001",
    )


class _FakeProvider:
    def __init__(
        self, *, content: str | None = None, error: Exception | None = None
    ) -> None:
        self._content = content
        self._error = error
        self.calls: list[tuple[list[ChatMessage], str]] = []

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        self.calls.append((messages, model))
        if self._error is not None:
            raise self._error
        return _response(self._content)


def _archivable() -> tuple[ChatMessage, ...]:
    return (
        ChatMessage(role=MessageRole.USER, content="design the API"),
        ChatMessage(role=MessageRole.ASSISTANT, content="proposed REST endpoints"),
    )


def _summarizer(provider: _FakeProvider) -> LLMSummarizer:
    return LLMSummarizer(
        provider=provider, model="example-small-001", temperature=0.3, max_tokens=100
    )


class TestLLMSummarizer:
    async def test_returns_llm_content(self) -> None:
        provider = _FakeProvider(content="A concise semantic summary.")
        out = await _summarizer(provider).summarize(
            _archivable(), execution_id="exec-1", fallback_text=_FALLBACK
        )
        assert out == "A concise semantic summary."
        assert provider.calls

    async def test_empty_content_falls_back(self) -> None:
        provider = _FakeProvider(content="   ")
        out = await _summarizer(provider).summarize(
            _archivable(), execution_id="exec-1", fallback_text=_FALLBACK
        )
        assert out == _FALLBACK

    async def test_provider_error_falls_back(self) -> None:
        provider = _FakeProvider(error=RuntimeError("provider down"))
        out = await _summarizer(provider).summarize(
            _archivable(), execution_id="exec-1", fallback_text=_FALLBACK
        )
        assert out == _FALLBACK

    async def test_empty_transcript_skips_provider(self) -> None:
        provider = _FakeProvider(content="unused")
        empty = (ChatMessage(role=MessageRole.ASSISTANT, content="   "),)
        out = await _summarizer(provider).summarize(
            empty, execution_id="exec-1", fallback_text=_FALLBACK
        )
        assert out == _FALLBACK
        assert not provider.calls
