"""Tests for fine-tune query-generation strategies."""

from collections.abc import AsyncIterator, Mapping

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.memory.embedding.fine_tune_query import (
    ExtractiveQueryGenerator,
    LlmQueryGenerator,
    build_query_generator,
    extractive_query,
)
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.errors import (
    AuthenticationError,
    ProviderInternalError,
)
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolDefinition,
)

pytestmark = pytest.mark.unit


class _StubProvider:
    """Concrete ``CompletionProvider`` whose ``complete`` is scripted.

    ``complete`` returns ``response`` or, when ``error`` is set, raises it.
    The streaming and capability methods are never exercised here.
    """

    def __init__(
        self,
        *,
        response: CompletionResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self.calls: list[tuple[list[ChatMessage], str]] = []

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        self.calls.append((messages, model))
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response

    async def stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError  # pragma: no cover - unused

    async def get_model_capabilities(self, model: str) -> ModelCapabilities:
        raise NotImplementedError  # pragma: no cover - unused

    async def batch_get_capabilities(
        self,
        models: tuple[str, ...],
    ) -> Mapping[str, ModelCapabilities | None]:
        raise NotImplementedError  # pragma: no cover - unused


def _response(content: str | None) -> CompletionResponse:
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=10, output_tokens=5, cost=0.001),
        model="test-basic-001",
    )


class TestExtractiveQuery:
    def test_uses_lead_sentence(self) -> None:
        assert extractive_query("First sentence. Second.") == (
            "Find information about: First sentence"
        )

    def test_bounds_long_lead(self) -> None:
        query = extractive_query("x" * 500)
        # "Find information about: " prefix plus a 200-char bounded snippet.
        assert query.startswith("Find information about: ")
        assert len(query) <= len("Find information about: ") + 200


class TestBuildQueryGenerator:
    def test_no_model_returns_extractive(self) -> None:
        gen = build_query_generator(provider=_StubProvider(), model=None)
        assert isinstance(gen, ExtractiveQueryGenerator)

    def test_blank_model_returns_extractive(self) -> None:
        gen = build_query_generator(provider=_StubProvider(), model="   ")
        assert isinstance(gen, ExtractiveQueryGenerator)

    def test_no_provider_returns_extractive(self) -> None:
        gen = build_query_generator(provider=None, model="test-basic-001")
        assert isinstance(gen, ExtractiveQueryGenerator)

    def test_provider_and_model_returns_llm(self) -> None:
        gen = build_query_generator(
            provider=_StubProvider(response=_response("q")),
            model="test-basic-001",
        )
        assert isinstance(gen, LlmQueryGenerator)


class TestExtractiveQueryGenerator:
    async def test_generate(self) -> None:
        gen = ExtractiveQueryGenerator()
        assert await gen.generate("Alpha beta. gamma") == (
            "Find information about: Alpha beta"
        )


class TestLlmQueryGenerator:
    def test_blank_model_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-blank"):
            LlmQueryGenerator(provider=_StubProvider(), model="   ")

    async def test_uses_llm_completion(self) -> None:
        provider = _StubProvider(response=_response("what is the onboarding policy"))
        gen = LlmQueryGenerator(provider=provider, model="test-basic-001")
        result = await gen.generate("New hires complete onboarding in week one.")
        assert result == "what is the onboarding policy"
        assert len(provider.calls) == 1

    async def test_strips_quotes_and_takes_first_line(self) -> None:
        provider = _StubProvider(
            response=_response('"the answer"\nignored second line'),
        )
        gen = LlmQueryGenerator(provider=provider, model="test-basic-001")
        assert await gen.generate("body") == "the answer"

    async def test_empty_completion_falls_back_to_extractive(self) -> None:
        provider = _StubProvider(response=_response("   "))
        gen = LlmQueryGenerator(provider=provider, model="test-basic-001")
        result = await gen.generate("First sentence. rest")
        assert result == "Find information about: First sentence"

    async def test_retryable_error_falls_back_to_extractive(self) -> None:
        provider = _StubProvider(error=ProviderInternalError("transient"))
        gen = LlmQueryGenerator(provider=provider, model="test-basic-001")
        result = await gen.generate("First sentence. rest")
        assert result == "Find information about: First sentence"

    async def test_non_retryable_error_propagates(self) -> None:
        provider = _StubProvider(error=AuthenticationError("bad key"))
        gen = LlmQueryGenerator(provider=provider, model="test-basic-001")
        with pytest.raises(AuthenticationError):
            await gen.generate("body")

    async def test_unexpected_error_propagates_not_silent_fallback(self) -> None:
        """A non-provider exception is a defect: it propagates, never degrades.

        Masking it as an extractive fallback would silently corrupt every
        chunk while looking like routine behaviour, so the broad handler
        re-raises after logging.
        """
        provider = _StubProvider(error=RuntimeError("broken wiring"))
        gen = LlmQueryGenerator(provider=provider, model="test-basic-001")
        with pytest.raises(RuntimeError, match="broken wiring"):
            await gen.generate("First sentence. rest")
