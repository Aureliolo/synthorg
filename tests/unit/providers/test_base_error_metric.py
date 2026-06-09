"""Tests for provider-error metric emission in ``BaseCompletionProvider``.

When an underlying driver raises, the base class must classify the
exception (via ``classify_provider_error``) and emit
``synthorg_provider_errors_total`` before re-raising -- so operators
see a per-class error rate even when the caller only catches the
re-raised exception.
"""

from collections.abc import AsyncIterator, Callable
from typing import override
from unittest.mock import MagicMock

import pytest

from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.errors import (
    AuthenticationError,
    ContentFilterError,
    DriverNotRegisteredError,
    InvalidRequestError,
    ModelNotFoundError,
    ProviderConnectionError,
    ProviderInternalError,
    ProviderTimeoutError,
    RateLimitError,
    classify_provider_error,
)
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    ToolDefinition,
)

pytestmark = pytest.mark.unit


class _ErroringProvider(BaseCompletionProvider):
    """Provider that raises the configured exception on every call."""

    def __init__(self, *, exc: Exception, provider_name: str = "errp") -> None:
        super().__init__()
        self._provider_name = provider_name
        self._exc = exc

    @override
    async def _do_complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        raise self._exc

    @override
    async def _do_stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        raise self._exc

    @override
    async def _do_get_model_capabilities(
        self,
        model: str,
    ) -> ModelCapabilities:
        raise self._exc


class _SuccessProvider(BaseCompletionProvider):
    """Provider that returns a canned successful response."""

    def __init__(self, *, provider_name: str = "okp") -> None:
        super().__init__()
        self._provider_name = provider_name

    @override
    async def _do_complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        from synthorg.providers.models import TokenUsage

        return CompletionResponse(
            content="hello",
            tool_calls=(),
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=1, output_tokens=1, cost=0.0),
            model=model,
        )

    @override
    async def _do_stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        async def _gen() -> AsyncIterator[StreamChunk]:
            if False:
                yield  # type: ignore[unreachable]

        return _gen()

    @override
    async def _do_get_model_capabilities(self, model: str) -> ModelCapabilities:
        msg = "not implemented"
        raise NotImplementedError(msg)


async def test_complete_emits_provider_error_with_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``complete`` classifies the raised exception before re-raising."""
    recorder = MagicMock()
    monkeypatch.setattr(
        "synthorg.providers.base.record_provider_error",
        recorder,
    )

    provider = _ErroringProvider(exc=RateLimitError("throttled"))

    with pytest.raises(RateLimitError):
        await provider.complete(
            [ChatMessage(role=MessageRole.USER, content="hi")],
            "test-large-001",
        )

    recorder.assert_called_once_with(
        provider="errp",
        model="test-large-001",
        error_class="rate_limit",
    )


async def test_stream_emits_provider_error_with_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``stream`` classifies the raised exception before re-raising."""
    recorder = MagicMock()
    monkeypatch.setattr(
        "synthorg.providers.base.record_provider_error",
        recorder,
    )

    provider = _ErroringProvider(
        exc=ProviderConnectionError("no route"),
    )

    with pytest.raises(ProviderConnectionError):
        await provider.stream(
            [ChatMessage(role=MessageRole.USER, content="hi")],
            "test-large-001",
        )

    recorder.assert_called_once_with(
        provider="errp",
        model="test-large-001",
        error_class="connection",
    )


async def test_success_does_not_emit_error_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful completions never increment the error counter."""
    recorder = MagicMock()
    monkeypatch.setattr(
        "synthorg.providers.base.record_provider_error",
        recorder,
    )

    provider = _SuccessProvider()
    result = await provider.complete(
        [ChatMessage(role=MessageRole.USER, content="hi")],
        "test-large-001",
    )
    assert result.content == "hello"
    recorder.assert_not_called()


def test_provider_label_defaults_to_class_name() -> None:
    """Subclasses without ``provider_name`` fall back to the class name."""

    class _Unbranded(BaseCompletionProvider):
        @override
        async def _do_complete(self, *a: object, **kw: object) -> CompletionResponse:
            raise NotImplementedError

        @override
        async def _do_stream(
            self,
            *a: object,
            **kw: object,
        ) -> AsyncIterator[StreamChunk]:
            raise NotImplementedError

        @override
        async def _do_get_model_capabilities(self, model: str) -> ModelCapabilities:
            raise NotImplementedError

    p = _Unbranded()
    assert p._provider_label() == "_Unbranded"


# -- classify_provider_error mapping coverage -------------------------------
#
# Covers every ProviderError subclass in ``_ERROR_CLASS_MAP`` plus the
# fallback paths for unmapped ``ProviderError`` subclasses and entirely
# unknown exception types. Keeps the Prometheus label set bounded by
# proof: any change to the map that loses a subclass mapping fails here.


@pytest.mark.parametrize(
    ("exc_factory", "expected_label"),
    [
        (lambda: RateLimitError("rate"), "rate_limit"),
        (lambda: ProviderTimeoutError("slow"), "timeout"),
        (lambda: ProviderConnectionError("no route"), "connection"),
        (lambda: ProviderInternalError("5xx"), "internal"),
        (lambda: InvalidRequestError("bad req"), "invalid_request"),
        (lambda: AuthenticationError("no creds"), "auth"),
        (lambda: ContentFilterError("blocked"), "content_filter"),
        (lambda: ModelNotFoundError("missing"), "not_found"),
    ],
)
def test_classify_provider_error_maps_every_canonical_subclass(
    exc_factory: Callable[[], Exception],
    expected_label: str,
) -> None:
    """Each canonical ``ProviderError`` subclass maps to its bounded label."""
    assert classify_provider_error(exc_factory()) == expected_label


def test_classify_provider_error_unmapped_subclass_returns_other() -> None:
    """``ProviderError`` subclasses outside the map fall into the ``other`` bucket."""
    assert classify_provider_error(DriverNotRegisteredError("absent")) == "other"


def test_classify_provider_error_unknown_exception_returns_other() -> None:
    """Arbitrary non-``ProviderError`` exceptions also bucket to ``other``."""

    class _NeverSeenError(Exception):
        pass

    assert classify_provider_error(_NeverSeenError("boom")) == "other"
    assert classify_provider_error(ValueError("stdlib")) == "other"
