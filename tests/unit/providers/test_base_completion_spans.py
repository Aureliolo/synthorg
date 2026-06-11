"""Regression: ``BaseCompletionProvider.complete`` opens a child span.

The span carries ``provider.{name,model,message_count,tool_count}``
attributes, plus ``provider.latency_ms`` on every exit path, and
``provider.retry_count`` when the retry handler observed retries.
Errors set ``exception.type`` and a scrubbed ``exception.message``
without invoking ``span.record_exception`` (per the prompt-safety
redaction rule).

Rather than wire the full OTel SDK, this test patches the module-level
``_tracer`` so we capture the calls without instantiating a
``TracerProvider`` (which has a one-shot global install guard). The
span object is a lightweight stand-in that records every method call
so we can assert the attributes and absence of forbidden calls.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import cast, override
from unittest.mock import patch

import pytest

import synthorg.providers.base as _base
from synthorg.core.completion_enums import FinishReason
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.enums import MessageRole
from synthorg.providers.errors import ProviderInternalError
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolDefinition,
)

pytestmark = pytest.mark.unit

_Completer = Callable[
    [list[ChatMessage], str, list[ToolDefinition] | None, CompletionConfig | None],
    Awaitable[CompletionResponse],
]


class _RecordingSpan:
    """Stand-in OTel span that records every method call."""

    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}
        self.recorded_exceptions: list[BaseException] = []
        self.statuses: list[object] = []

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def set_status(self, status: object) -> None:
        self.statuses.append(status)

    def record_exception(self, exc: BaseException) -> None:  # pragma: no cover
        self.recorded_exceptions.append(exc)


class _RecordingTracer:
    """Stand-in OTel tracer that captures every start_as_current_span call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.spans: list[_RecordingSpan] = []

    @contextmanager
    def start_as_current_span(
        self,
        name: str,
        *,
        attributes: dict[str, object] | None = None,
        record_exception: bool = True,
        set_status_on_exception: bool = True,
    ) -> Iterator[_RecordingSpan]:
        span = _RecordingSpan()
        if attributes:
            span.attributes.update(attributes)
        self.calls.append(
            {
                "name": name,
                "attributes": dict(attributes or {}),
                "record_exception": record_exception,
                "set_status_on_exception": set_status_on_exception,
            },
        )
        self.spans.append(span)
        yield span


class _StubProvider(BaseCompletionProvider):
    """Minimal concrete provider that delegates ``_do_complete`` to a closure."""

    def __init__(
        self,
        *,
        completer: _Completer,
        provider_label: str = "stub-provider",
    ) -> None:
        super().__init__()
        self._completer = completer
        self._label = provider_label

    @override
    def _provider_label(self) -> str:
        return self._label

    @override
    async def _do_complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        result: CompletionResponse = await self._completer(
            messages, model, tools, config
        )
        return result

    @override
    async def _do_stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:  # pragma: no cover
        # Stub never streams; satisfy the abstract signature.
        raise NotImplementedError

    @override
    async def _do_get_model_capabilities(
        self,
        model: str,
    ) -> ModelCapabilities:  # pragma: no cover
        raise NotImplementedError


def _ok_response() -> CompletionResponse:
    return CompletionResponse(
        content="ok",
        model="example-medium-001",
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(
            input_tokens=10,
            output_tokens=2,
            cost=0.0,
        ),
    )


class TestProviderCompleteSpan:
    async def test_happy_path_emits_child_span_with_attributes(self) -> None:
        async def completer(_messages, _model, _tools, _config):  # type: ignore[no-untyped-def]
            return _ok_response()

        tracer = _RecordingTracer()
        provider = _StubProvider(completer=completer)
        with patch.object(_base, "_tracer", tracer):
            result = await provider.complete(
                messages=[ChatMessage(role=MessageRole.USER, content="hello")],
                model="example-medium-001",
            )

        assert result.content == "ok"
        assert len(tracer.calls) == 1
        call = tracer.calls[0]
        assert call["name"] == "provider.complete"
        assert call["record_exception"] is False
        assert call["set_status_on_exception"] is False
        # Attributes set at span open.
        attrs = cast("dict[str, object]", call["attributes"])
        assert attrs["provider.name"] == "stub-provider"
        assert attrs["provider.model"] == "example-medium-001"
        assert attrs["provider.message_count"] == 1
        assert attrs["provider.tool_count"] == 0
        # Latency is set on the span's attributes dict by ``set_attribute``.
        span = tracer.spans[0]
        assert "provider.latency_ms" in span.attributes
        assert span.attributes["provider.latency_ms"] >= 0.0  # type: ignore[operator]
        # No exception was recorded via the SDK's auto-handler.
        assert not span.recorded_exceptions

    async def test_error_path_sets_exception_attrs_without_record_exception(
        self,
    ) -> None:
        async def completer(_messages, _model, _tools, _config):  # type: ignore[no-untyped-def]
            msg = "synthetic provider outage"
            raise ProviderInternalError(msg)

        tracer = _RecordingTracer()
        provider = _StubProvider(completer=completer)
        with (
            patch.object(_base, "_tracer", tracer),
            pytest.raises(ProviderInternalError),
        ):
            await provider.complete(
                messages=[ChatMessage(role=MessageRole.USER, content="hello")],
                model="example-medium-001",
            )

        span = tracer.spans[0]
        # Exception fields set via set_attribute, NOT record_exception.
        assert span.attributes["exception.type"] == "ProviderInternalError"
        assert "synthetic provider outage" in span.attributes["exception.message"]  # type: ignore[operator]
        # Latency was still set on the error path.
        assert "provider.latency_ms" in span.attributes
        # record_exception MUST NOT have been called (SEC: traceback
        # frame-locals can leak credentials).
        assert not span.recorded_exceptions
        # Span status MUST be ERROR. ``set_status_on_exception=False``
        # opts out of the SDK's auto-status (which would have stamped
        # an un-scrubbed ``str(exc)``), so the manual ``set_status``
        # call is the only thing that flips the span from UNSET to
        # ERROR for observability dashboards.
        from opentelemetry.trace import StatusCode

        assert span.statuses, "expected span.set_status to be called on error"
        assert span.statuses[-1].status_code == StatusCode.ERROR  # type: ignore[attr-defined]
