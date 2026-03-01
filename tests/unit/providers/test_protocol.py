"""Tests for CompletionProvider protocol and BaseCompletionProvider ABC."""

from collections.abc import AsyncIterator  # noqa: TC003

import pytest

from ai_company.constants import BUDGET_ROUNDING_PRECISION
from ai_company.providers.base import BaseCompletionProvider
from ai_company.providers.capabilities import ModelCapabilities
from ai_company.providers.enums import FinishReason, MessageRole, StreamEventType
from ai_company.providers.errors import InvalidRequestError
from ai_company.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolDefinition,
)
from ai_company.providers.protocol import CompletionProvider

from .conftest import FakeProvider, ModelCapabilitiesFactory, TokenUsageFactory

pytestmark = pytest.mark.timeout(30)


# ── Protocol structural typing ────────────────────────────────────


@pytest.mark.unit
class TestCompletionProviderProtocol:
    """Tests that the Protocol works for structural type checking."""

    def test_fake_provider_is_instance(self, fake_provider: FakeProvider) -> None:
        assert isinstance(fake_provider, CompletionProvider)

    def test_non_provider_not_instance(self) -> None:
        assert not isinstance("not a provider", CompletionProvider)

    def test_dict_not_instance(self) -> None:
        assert not isinstance({}, CompletionProvider)

    async def test_complete_returns_response(
        self,
        fake_provider: FakeProvider,
    ) -> None:
        msg = ChatMessage(role=MessageRole.USER, content="Hi")
        resp = await fake_provider.complete([msg], "test-model")
        assert isinstance(resp, CompletionResponse)

    async def test_stream_returns_async_iterator(
        self,
        fake_provider: FakeProvider,
    ) -> None:
        msg = ChatMessage(role=MessageRole.USER, content="Hi")
        stream = await fake_provider.stream([msg], "test-model")
        chunks = [chunk async for chunk in stream]
        assert len(chunks) == 2
        assert chunks[0].event_type == StreamEventType.CONTENT_DELTA
        assert chunks[1].event_type == StreamEventType.DONE

    async def test_get_model_capabilities(
        self,
        fake_provider: FakeProvider,
        sample_model_capabilities: ModelCapabilities,
    ) -> None:
        caps = await fake_provider.get_model_capabilities("test-model")
        assert caps.model_id == sample_model_capabilities.model_id

    async def test_complete_records_call(
        self,
        fake_provider: FakeProvider,
    ) -> None:
        msg = ChatMessage(role=MessageRole.USER, content="Hi")
        await fake_provider.complete([msg], "my-model")
        assert len(fake_provider.complete_calls) == 1
        assert fake_provider.complete_calls[0][1] == "my-model"


# ── BaseCompletionProvider ABC ────────────────────────────────────


class _ConcreteProvider(BaseCompletionProvider):
    """Concrete subclass of BaseCompletionProvider for testing."""

    def __init__(self) -> None:
        self._caps = ModelCapabilitiesFactory.build()

    async def _do_complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        return CompletionResponse(
            content="test response",
            finish_reason=FinishReason.STOP,
            usage=TokenUsageFactory.build(),
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
            yield StreamChunk(
                event_type=StreamEventType.CONTENT_DELTA,
                content="streamed",
            )
            yield StreamChunk(event_type=StreamEventType.DONE)

        return _gen()

    async def _do_get_model_capabilities(self, model: str) -> ModelCapabilities:
        return self._caps


class _RecordingProvider(BaseCompletionProvider):
    """Records all arguments passed to hooks for forwarding verification."""

    def __init__(self) -> None:
        self._caps = ModelCapabilitiesFactory.build()
        self.last_complete_kwargs: dict[str, object] = {}
        self.last_stream_kwargs: dict[str, object] = {}

    async def _do_complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        self.last_complete_kwargs = {"tools": tools, "config": config}
        return CompletionResponse(
            content="ok",
            finish_reason=FinishReason.STOP,
            usage=TokenUsageFactory.build(),
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
        self.last_stream_kwargs = {"tools": tools, "config": config}

        async def _gen() -> AsyncIterator[StreamChunk]:
            yield StreamChunk(event_type=StreamEventType.DONE)

        return _gen()

    async def _do_get_model_capabilities(self, model: str) -> ModelCapabilities:
        return self._caps


@pytest.mark.unit
class TestBaseCompletionProvider:
    """Tests for BaseCompletionProvider validation and helpers."""

    async def test_complete_delegates_to_hook(self) -> None:
        provider = _ConcreteProvider()
        msg = ChatMessage(role=MessageRole.USER, content="Hello")
        resp = await provider.complete([msg], "test-model")
        assert resp.content == "test response"
        assert resp.model == "test-model"

    async def test_stream_delegates_to_hook(self) -> None:
        provider = _ConcreteProvider()
        msg = ChatMessage(role=MessageRole.USER, content="Hello")
        stream = await provider.stream([msg], "test-model")
        chunks = [chunk async for chunk in stream]
        assert len(chunks) == 2

    async def test_get_model_capabilities_delegates(self) -> None:
        provider = _ConcreteProvider()
        caps = await provider.get_model_capabilities("test-model")
        assert isinstance(caps, ModelCapabilities)

    async def test_complete_rejects_empty_messages(self) -> None:
        provider = _ConcreteProvider()
        with pytest.raises(InvalidRequestError, match="must not be empty"):
            await provider.complete([], "test-model")

    async def test_stream_rejects_empty_messages(self) -> None:
        provider = _ConcreteProvider()
        with pytest.raises(InvalidRequestError, match="must not be empty"):
            await provider.stream([], "test-model")

    def test_compute_cost_basic(self) -> None:
        usage = BaseCompletionProvider.compute_cost(
            1000,
            500,
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
        )
        assert isinstance(usage, TokenUsage)
        assert usage.input_tokens == 1000
        assert usage.output_tokens == 500
        assert usage.total_tokens == 1500
        expected = (1000 / 1000) * 0.003 + (500 / 1000) * 0.015
        assert abs(usage.cost_usd - expected) < 1e-9

    def test_compute_cost_zero(self) -> None:
        usage = BaseCompletionProvider.compute_cost(
            0,
            0,
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
        )
        assert usage.cost_usd == 0.0
        assert usage.total_tokens == 0

    def test_compute_cost_large_tokens(self) -> None:
        usage = BaseCompletionProvider.compute_cost(
            200_000,
            8_192,
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
        )
        assert usage.total_tokens == 208_192
        expected = (200_000 / 1000) * 0.003 + (8_192 / 1000) * 0.015
        assert abs(usage.cost_usd - expected) < 1e-9

    def test_base_satisfies_protocol(self) -> None:
        provider = _ConcreteProvider()
        assert isinstance(provider, CompletionProvider)

    def test_cannot_instantiate_abc_directly(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            BaseCompletionProvider()  # type: ignore[abstract]

    def test_partial_implementation_rejected(self) -> None:
        class _PartialProvider(BaseCompletionProvider):
            async def _do_complete(  # type: ignore[empty-body]
                self,
                messages: list[ChatMessage],
                model: str,
                **kwargs: object,
            ) -> CompletionResponse: ...

        with pytest.raises(TypeError, match="abstract"):
            _PartialProvider()  # type: ignore[abstract]

    async def test_complete_rejects_empty_messages_context(self) -> None:
        provider = _ConcreteProvider()
        with pytest.raises(InvalidRequestError) as exc_info:
            await provider.complete([], "test-model")
        assert exc_info.value.context == {"field": "messages"}

    async def test_complete_forwards_tools_and_config(self) -> None:
        provider = _RecordingProvider()
        msg = ChatMessage(role=MessageRole.USER, content="Hi")
        tool = ToolDefinition(name="ping")
        cfg = CompletionConfig(temperature=0.5)
        await provider.complete([msg], "m", tools=[tool], config=cfg)
        assert provider.last_complete_kwargs["tools"] == [tool]
        assert provider.last_complete_kwargs["config"] == cfg

    async def test_stream_forwards_tools_and_config(self) -> None:
        provider = _RecordingProvider()
        msg = ChatMessage(role=MessageRole.USER, content="Hi")
        tool = ToolDefinition(name="ping")
        cfg = CompletionConfig(temperature=0.5)
        stream = await provider.stream([msg], "m", tools=[tool], config=cfg)
        _ = [chunk async for chunk in stream]
        assert provider.last_stream_kwargs["tools"] == [tool]
        assert provider.last_stream_kwargs["config"] == cfg

    async def test_complete_rejects_none_model(self) -> None:
        provider = _ConcreteProvider()
        msg = ChatMessage(role=MessageRole.USER, content="Hi")
        with pytest.raises(InvalidRequestError, match="non-blank"):
            await provider.complete([msg], None)  # type: ignore[arg-type]

    async def test_complete_rejects_blank_model(self) -> None:
        provider = _ConcreteProvider()
        msg = ChatMessage(role=MessageRole.USER, content="Hi")
        with pytest.raises(InvalidRequestError, match="non-blank"):
            await provider.complete([msg], "")

    async def test_stream_rejects_blank_model(self) -> None:
        provider = _ConcreteProvider()
        msg = ChatMessage(role=MessageRole.USER, content="Hi")
        with pytest.raises(InvalidRequestError, match="non-blank"):
            await provider.stream([msg], "  ")

    async def test_get_capabilities_rejects_blank_model(self) -> None:
        provider = _ConcreteProvider()
        with pytest.raises(InvalidRequestError, match="non-blank"):
            await provider.get_model_capabilities("")

    def test_compute_cost_negative_input_tokens(self) -> None:
        with pytest.raises(InvalidRequestError, match="input_tokens"):
            BaseCompletionProvider.compute_cost(
                -1,
                100,
                cost_per_1k_input=0.003,
                cost_per_1k_output=0.015,
            )

    def test_compute_cost_negative_output_tokens(self) -> None:
        with pytest.raises(InvalidRequestError, match="output_tokens"):
            BaseCompletionProvider.compute_cost(
                100,
                -1,
                cost_per_1k_input=0.003,
                cost_per_1k_output=0.015,
            )

    def test_compute_cost_negative_input_rate(self) -> None:
        with pytest.raises(InvalidRequestError, match="cost_per_1k_input"):
            BaseCompletionProvider.compute_cost(
                100,
                100,
                cost_per_1k_input=-0.003,
                cost_per_1k_output=0.015,
            )

    def test_compute_cost_negative_output_rate(self) -> None:
        with pytest.raises(InvalidRequestError, match="cost_per_1k_output"):
            BaseCompletionProvider.compute_cost(
                100,
                100,
                cost_per_1k_input=0.003,
                cost_per_1k_output=-0.015,
            )

    def test_compute_cost_rounding_precision(self) -> None:
        usage = BaseCompletionProvider.compute_cost(
            333,
            777,
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
        )
        expected = round(
            (333 / 1000) * 0.003 + (777 / 1000) * 0.015,
            BUDGET_ROUNDING_PRECISION,
        )
        assert usage.cost_usd == expected

    def test_compute_cost_inf_input_rate_rejected(self) -> None:
        with pytest.raises(InvalidRequestError, match="finite"):
            BaseCompletionProvider.compute_cost(
                100,
                100,
                cost_per_1k_input=float("inf"),
                cost_per_1k_output=0.015,
            )

    def test_compute_cost_nan_output_rate_rejected(self) -> None:
        with pytest.raises(InvalidRequestError, match="finite"):
            BaseCompletionProvider.compute_cost(
                100,
                100,
                cost_per_1k_input=0.003,
                cost_per_1k_output=float("nan"),
            )
