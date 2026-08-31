"""Unit tests for the LLM-backed semantic compaction summariser."""

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.engine.compaction.llm_summarizer import (
    _DEFAULT_MAX_TOKENS,
    _DEFAULT_TEMPERATURE,
    LLMSummarizer,
)
from synthorg.engine.compaction.models import CompactionConfig
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
        model="example-basic-001",
    )


class _FakeProvider:
    def __init__(
        self, *, content: str | None = None, error: Exception | None = None
    ) -> None:
        self._content = content
        self._error = error
        self.calls: list[tuple[list[ChatMessage], str, CompletionConfig | None]] = []

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        self.calls.append((messages, model, config))
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
        provider=provider, model="example-basic-001", temperature=0.3, max_tokens=100
    )


class TestLLMSummarizer:
    async def test_returns_llm_content(self) -> None:
        provider = _FakeProvider(content="A concise semantic summary.")
        out = await _summarizer(provider).summarize(
            _archivable(), fallback_text=_FALLBACK
        )
        assert out == "A concise semantic summary."
        assert provider.calls

    async def test_default_sampling_params_match_compaction_defaults(self) -> None:
        provider = _FakeProvider(content="summary")
        summarizer = LLMSummarizer(provider=provider, model="example-basic-001")
        await summarizer.summarize(_archivable(), fallback_text=_FALLBACK)
        _, _, config = provider.calls[0]
        assert config is not None
        assert config.temperature == pytest.approx(0.3)
        assert config.max_tokens == 500

    async def test_explicit_args_override_defaults(self) -> None:
        provider = _FakeProvider(content="summary")
        summarizer = LLMSummarizer(
            provider=provider,
            model="example-basic-001",
            temperature=0.7,
            max_tokens=250,
        )
        await summarizer.summarize(_archivable(), fallback_text=_FALLBACK)
        _, _, config = provider.calls[0]
        assert config is not None
        assert config.temperature == pytest.approx(0.7)
        assert config.max_tokens == 250

    async def test_preserve_markers_true_adds_the_instruction(self) -> None:
        """An agent's explicit request must reach the LLM path, not only the
        text-only fallback path -- the bug this test guards against left the
        override silently discarded whenever the summariser succeeded."""
        provider = _FakeProvider(content="summary")
        await _summarizer(provider).summarize(
            _archivable(), fallback_text=_FALLBACK, preserve_markers=True
        )
        messages, _, _ = provider.calls[0]
        system_content = messages[0].content or ""
        assert "epistemic markers" in system_content.lower()

    async def test_preserve_markers_false_omits_the_instruction(self) -> None:
        provider = _FakeProvider(content="summary")
        await _summarizer(provider).summarize(
            _archivable(), fallback_text=_FALLBACK, preserve_markers=False
        )
        messages, _, _ = provider.calls[0]
        system_content = messages[0].content or ""
        assert "epistemic markers" not in system_content.lower()

    def test_module_defaults_mirror_compaction_config(self) -> None:
        # The module Finals are duplicated from the domain config; guard the
        # duplication so a CompactionConfig default change cannot silently
        # diverge a directly-constructed summariser from the wired path.
        defaults = CompactionConfig()
        assert defaults.llm_summary_temperature == _DEFAULT_TEMPERATURE
        assert defaults.llm_summary_max_tokens == _DEFAULT_MAX_TOKENS

    async def test_empty_content_falls_back(self) -> None:
        provider = _FakeProvider(content="   ")
        out = await _summarizer(provider).summarize(
            _archivable(), fallback_text=_FALLBACK
        )
        assert out == _FALLBACK

    async def test_provider_error_falls_back(self) -> None:
        provider = _FakeProvider(error=RuntimeError("provider down"))
        out = await _summarizer(provider).summarize(
            _archivable(), fallback_text=_FALLBACK
        )
        assert out == _FALLBACK

    async def test_empty_transcript_skips_provider(self) -> None:
        provider = _FakeProvider(content="unused")
        empty = (ChatMessage(role=MessageRole.ASSISTANT, content="   "),)
        out = await _summarizer(provider).summarize(empty, fallback_text=_FALLBACK)
        assert out == _FALLBACK
        assert not provider.calls
