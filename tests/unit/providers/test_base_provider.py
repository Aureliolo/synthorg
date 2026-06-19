"""Tests for BaseCompletionProvider logging."""

import asyncio
from collections.abc import AsyncIterator
from typing import override

import pytest
import structlog

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.config import BudgetConfig
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.tracker import CostTracker
from synthorg.core.completion_enums import FinishReason
from synthorg.core.types import NotBlankStr
from synthorg.observability.events.provider import (
    PROVIDER_BATCH_CAPABILITIES_PARTIAL,
    PROVIDER_CALL_ERROR,
    PROVIDER_CALL_START,
    PROVIDER_CALL_SUCCESS,
    PROVIDER_STREAM_START,
)
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole, StreamEventType
from synthorg.providers.errors import InvalidRequestError, ProviderInternalError
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolDefinition,
)


class _StubProvider(BaseCompletionProvider):
    """Minimal concrete provider for testing the base class."""

    @override
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
                cost=0.0,
            ),
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
            return
            yield  # make it an async generator  # type: ignore[unreachable]

        return _gen()

    @override
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


@pytest.mark.unit
class TestBaseProviderMetadataEnrichment:
    """BaseCompletionProvider injects _synthorg_* keys into provider_metadata."""

    async def test_latency_ms_injected(self) -> None:
        """_synthorg_latency_ms is a non-negative float."""
        provider = _StubProvider()
        response = await provider.complete([_msg()], "test-model")
        assert "_synthorg_latency_ms" in response.provider_metadata
        assert isinstance(response.provider_metadata["_synthorg_latency_ms"], float)
        assert response.provider_metadata["_synthorg_latency_ms"] >= 0.0

    async def test_no_retry_handler_no_retry_keys(self) -> None:
        """Without a retry handler, retry keys are absent."""
        provider = _StubProvider()
        response = await provider.complete([_msg()], "test-model")
        assert "_synthorg_retry_count" not in response.provider_metadata
        assert "_synthorg_retry_reason" not in response.provider_metadata

    async def test_retry_handler_zero_retries_on_first_success(self) -> None:
        """With a retry handler, _synthorg_retry_count=0 when no retries needed."""
        from synthorg.core.resilience_config import RetryConfig
        from synthorg.providers.resilience.retry import RetryHandler

        config = RetryConfig(
            max_retries=3,
            base_delay=0.001,
            max_delay=0.001,
            exponential_base=2.0,
            jitter=False,
        )
        provider = _StubProvider(retry_handler=RetryHandler(config))
        response = await provider.complete([_msg()], "test-model")
        assert response.provider_metadata["_synthorg_retry_count"] == 0
        assert "_synthorg_retry_reason" not in response.provider_metadata

    async def test_retry_handler_retry_count_reflects_attempts(self) -> None:
        """_synthorg_retry_count equals retry attempts (attempts - 1)."""
        from synthorg.core.resilience_config import RetryConfig
        from synthorg.providers.errors import RateLimitError
        from synthorg.providers.resilience.retry import RetryHandler

        config = RetryConfig(
            max_retries=3,
            base_delay=0.001,
            max_delay=0.001,
            exponential_base=2.0,
            jitter=False,
        )

        calls = 0

        class _RetryableProvider(_StubProvider):
            @override
            async def _do_complete(
                self,
                messages: list[ChatMessage],
                model: str,
                *,
                tools: object | None = None,
                config: object | None = None,
            ) -> CompletionResponse:
                nonlocal calls
                calls += 1
                if calls < 3:
                    raise RateLimitError("retry me")  # noqa: TRY003, EM101
                return await super()._do_complete(messages, model)

        provider = _RetryableProvider(retry_handler=RetryHandler(config))
        response = await provider.complete([_msg()], "test-model")
        assert response.provider_metadata["_synthorg_retry_count"] == 2
        assert response.provider_metadata["_synthorg_retry_reason"] == "RateLimitError"


def _caps(model_id: str) -> ModelCapabilities:
    return ModelCapabilities(
        model_id=model_id,
        provider="test-provider",
        max_context_tokens=1000,
        max_output_tokens=500,
        cost_per_1k_input=0.001,
        cost_per_1k_output=0.002,
    )


@pytest.mark.unit
class TestBatchGetCapabilitiesDefault:
    """``BaseCompletionProvider.batch_get_capabilities`` default impl."""

    async def test_empty_models_returns_empty_dict(self) -> None:
        provider = _StubProvider()
        result = await provider.batch_get_capabilities(())
        assert result == {}

    async def test_returns_per_model_capabilities(self) -> None:
        class _Provider(_StubProvider):
            @override
            async def _do_get_model_capabilities(
                self,
                model: str,
            ) -> ModelCapabilities:
                return _caps(model)

        provider = _Provider()
        result = await provider.batch_get_capabilities(("alpha", "beta"))
        assert set(result) == {"alpha", "beta"}
        assert result["alpha"] is not None
        assert result["alpha"].model_id == "alpha"
        assert result["beta"] is not None
        assert result["beta"].model_id == "beta"

    async def test_per_model_failures_become_none(self) -> None:
        class _PartialProvider(_StubProvider):
            @override
            async def _do_get_model_capabilities(
                self,
                model: str,
            ) -> ModelCapabilities:
                if model == "broken":
                    msg = "boom"
                    raise ProviderInternalError(msg)
                return _caps(model)

        provider = _PartialProvider()
        with structlog.testing.capture_logs() as cap:
            result = await provider.batch_get_capabilities(("ok", "broken"))

        assert result["ok"] is not None
        assert result["broken"] is None
        partials = [
            e for e in cap if e.get("event") == PROVIDER_BATCH_CAPABILITIES_PARTIAL
        ]
        assert len(partials) == 1
        assert partials[0]["model"] == "broken"
        assert partials[0]["error_type"] == "ProviderInternalError"

    async def test_runs_in_parallel(self) -> None:
        # Deterministic concurrency assertion: every probe blocks on a
        # shared gate that only opens once all probes have signalled they
        # are in-flight. Sequential execution would deadlock on the gate;
        # parallel execution proceeds. No wall-clock timing.
        models = tuple(f"m{i}" for i in range(5))
        in_flight = 0
        peak_in_flight = 0
        gate = asyncio.Event()
        lock = asyncio.Lock()

        class _GatedProvider(_StubProvider):
            @override
            async def _do_get_model_capabilities(
                self,
                model: str,
            ) -> ModelCapabilities:
                nonlocal in_flight, peak_in_flight
                async with lock:
                    in_flight += 1
                    peak_in_flight = max(peak_in_flight, in_flight)
                    if in_flight == len(models):
                        gate.set()
                await gate.wait()
                async with lock:
                    in_flight -= 1
                return _caps(model)

        provider = _GatedProvider()
        result = await provider.batch_get_capabilities(models)
        assert peak_in_flight == len(models)
        assert set(result) == set(models)

    async def test_propagates_memory_error(self) -> None:
        class _BadProvider(_StubProvider):
            @override
            async def _do_get_model_capabilities(
                self,
                model: str,
            ) -> ModelCapabilities:
                raise MemoryError

        provider = _BadProvider()
        with pytest.raises(BaseExceptionGroup) as exc_info:
            await provider.batch_get_capabilities(("doomed",))
        # TaskGroup wraps escaped exceptions; one of them is the MemoryError.
        assert any(isinstance(exc, MemoryError) for exc in exc_info.value.exceptions)


class _UsageStreamProvider(_StubProvider):
    """Stub whose stream yields a content delta then a terminal USAGE chunk."""

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
            yield StreamChunk(event_type=StreamEventType.CONTENT_DELTA, content="hi")
            yield StreamChunk(
                event_type=StreamEventType.USAGE,
                usage=TokenUsage(input_tokens=12, output_tokens=7, cost=0.0),
            )

        return _gen()


class _ErrorBeforeUsageStreamProvider(_StubProvider):
    """Stub whose stream errors mid-flight before emitting a USAGE chunk."""

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
            yield StreamChunk(event_type=StreamEventType.CONTENT_DELTA, content="hi")
            msg = "mid-stream boom"
            raise ProviderInternalError(msg)

        return _gen()


class _PostUsageStreamProvider(_StubProvider):
    """Stub that yields a trailing chunk AFTER the terminal USAGE chunk."""

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
            yield StreamChunk(
                event_type=StreamEventType.USAGE,
                usage=TokenUsage(input_tokens=3, output_tokens=4, cost=0.0),
            )
            yield StreamChunk(event_type=StreamEventType.CONTENT_DELTA, content="tail")

        return _gen()


@pytest.mark.unit
class TestBaseProviderStreamCostRecording:
    """``stream()`` records cost from the terminal USAGE chunk in scope."""

    async def test_drained_stream_emits_one_cost_record(self) -> None:
        provider = _UsageStreamProvider()
        tracker = CostTracker(budget_config=BudgetConfig(currency=DEFAULT_CURRENCY))
        async with cost_recording_scope(
            cost_tracker=tracker,
            agent_id=NotBlankStr("system"),
            task_id=NotBlankStr("system:test:stream"),
            call_category=LLMCallCategory.SYSTEM,
        ):
            iterator = await provider.stream([_msg()], "test-model")
            chunks = [chunk async for chunk in iterator]

        # The wrapper is transparent: both chunks pass through in order.
        assert [c.event_type for c in chunks] == [
            StreamEventType.CONTENT_DELTA,
            StreamEventType.USAGE,
        ]
        await tracker.drain_pending_records()
        records = await tracker.get_records()
        assert len(records) == 1
        assert records[0].input_tokens == 12
        assert records[0].output_tokens == 7
        assert records[0].model == "test-model"

    async def test_no_scope_is_noop(self) -> None:
        provider = _UsageStreamProvider()
        iterator = await provider.stream([_msg()], "test-model")
        chunks = [chunk async for chunk in iterator]
        # Drains cleanly with no open scope and records nothing.
        assert len(chunks) == 2

    async def test_error_before_usage_records_nothing(self) -> None:
        provider = _ErrorBeforeUsageStreamProvider()
        tracker = CostTracker(budget_config=BudgetConfig(currency=DEFAULT_CURRENCY))
        async with cost_recording_scope(
            cost_tracker=tracker,
            agent_id=NotBlankStr("system"),
            task_id=NotBlankStr("system:test:stream-error"),
            call_category=LLMCallCategory.SYSTEM,
        ):
            iterator = await provider.stream([_msg()], "test-model")
            with pytest.raises(ProviderInternalError):
                async for _chunk in iterator:
                    pass
        await tracker.drain_pending_records()
        # A stream that errors before the USAGE chunk records nothing.
        assert len(await tracker.get_records()) == 0

    async def test_close_after_usage_records_nothing(self) -> None:
        provider = _PostUsageStreamProvider()
        tracker = CostTracker(budget_config=BudgetConfig(currency=DEFAULT_CURRENCY))
        async with cost_recording_scope(
            cost_tracker=tracker,
            agent_id=NotBlankStr("system"),
            task_id=NotBlankStr("system:test:stream-early-close"),
            call_category=LLMCallCategory.SYSTEM,
        ):
            iterator = await provider.stream([_msg()], "test-model")
            async for chunk in iterator:
                if chunk.event_type is StreamEventType.USAGE:
                    break
            # Close early, after the USAGE chunk but before the stream is
            # fully drained. Cost is recorded only on a complete drain: the
            # early ``aclose()`` raises GeneratorExit out of the wrapper's
            # ``finally`` (which still releases the inner rate-limiter slot)
            # and skips the synthetic cost record.
            await iterator.aclose()  # type: ignore[attr-defined]
        await tracker.drain_pending_records()
        assert len(await tracker.get_records()) == 0
