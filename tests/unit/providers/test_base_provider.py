"""Tests for BaseCompletionProvider logging."""

from typing import TYPE_CHECKING

import pytest
import structlog

from ai_company.observability.events import (
    PROVIDER_CALL_ERROR,
    PROVIDER_CALL_START,
    PROVIDER_CALL_SUCCESS,
    PROVIDER_STREAM_START,
)
from ai_company.providers.base import BaseCompletionProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from ai_company.providers.capabilities import ModelCapabilities
    from ai_company.providers.models import (
        ChatMessage,
        CompletionConfig,
        CompletionResponse,
        StreamChunk,
        ToolDefinition,
    )

from ai_company.providers.enums import FinishReason, MessageRole
from ai_company.providers.errors import InvalidRequestError
from ai_company.providers.models import (
    ChatMessage,
    CompletionResponse,
    TokenUsage,
)

pytestmark = pytest.mark.timeout(30)


class _StubProvider(BaseCompletionProvider):
    """Minimal concrete provider for testing the base class."""

    async def _do_complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        return CompletionResponse(
            content="hello",
            tool_calls=(),
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(
                input_tokens=10,
                output_tokens=5,
                cost_usd=0.0,
            ),
            model=model,
        )

    async def _do_stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        async def _gen() -> AsyncIterator[StreamChunk]:
            return
            yield  # make it an async generator  # type: ignore[unreachable]

        return _gen()

    async def _do_get_model_capabilities(
        self,
        model: str,
    ) -> ModelCapabilities:
        msg = "not implemented"
        raise NotImplementedError(msg)


def _msg(content: str = "hi") -> ChatMessage:
    return ChatMessage(role=MessageRole.USER, content=content)


@pytest.mark.unit
class TestBaseProviderLogging:
    async def test_complete_emits_call_start_and_success(self) -> None:
        provider = _StubProvider()
        with structlog.testing.capture_logs() as cap:
            await provider.complete([_msg()], "test-model")
        start = [e for e in cap if e.get("event") == PROVIDER_CALL_START]
        success = [e for e in cap if e.get("event") == PROVIDER_CALL_SUCCESS]
        assert len(start) == 1
        assert start[0]["model"] == "test-model"
        assert len(success) == 1

    async def test_stream_emits_stream_start(self) -> None:
        provider = _StubProvider()
        with structlog.testing.capture_logs() as cap:
            await provider.stream([_msg()], "test-model")
        events = [e for e in cap if e.get("event") == PROVIDER_STREAM_START]
        assert len(events) == 1
        assert events[0]["model"] == "test-model"

    async def test_empty_messages_emits_error(self) -> None:
        provider = _StubProvider()
        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(InvalidRequestError),
        ):
            await provider.complete([], "test-model")
        events = [e for e in cap if e.get("event") == PROVIDER_CALL_ERROR]
        assert len(events) == 1

    async def test_blank_model_emits_error(self) -> None:
        provider = _StubProvider()
        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(InvalidRequestError),
        ):
            await provider.complete([_msg()], "  ")
        events = [e for e in cap if e.get("event") == PROVIDER_CALL_ERROR]
        assert len(events) == 1
