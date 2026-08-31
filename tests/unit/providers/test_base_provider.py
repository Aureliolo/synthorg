"""Tests for BaseCompletionProvider logging."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Final, override

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
from synthorg.providers.errors import (
    InvalidRequestError,
    ProviderInternalError,
    ProviderOverloadedError,
)
from synthorg.providers.health import (
    CallOutcome,
    ProviderHealthStatus,
    ProviderOutcomeClass,
    RecordSource,
)
from synthorg.providers.health_recording import (
    outcome_recorder_for,
    record_call_outcome,
)
from synthorg.providers.health_tracker import ProviderHealthTracker
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolDefinition,
)
from tests._shared import FakeClock

#: One reference instant for both the recorded timestamps and the window
#: they are aggregated over. Left to wall time, the two are read from
#: different moments, so the window boundary moves under the assertion.
_HEALTH_NOW: Final[datetime] = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


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
class TestCompletionOutcomesReachHealth:
    """Real traffic is evidence about whether a provider is serving.

    Without this the 24h error rate describes only the reachability sweep's
    own pings, so a provider answering every agent call could still read
    however the last ping left it.
    """

    async def test_a_successful_call_is_recorded(self) -> None:
        tracker = ProviderHealthTracker()
        clock = FakeClock(start=_HEALTH_NOW)
        provider = _StubProvider()
        provider.bind_health_recorder(
            outcome_recorder_for(tracker, "test-provider", clock=clock)
        )

        _ = await provider.complete([_msg()], "test-basic-001")

        summary = await tracker.get_summary("test-provider", now=_HEALTH_NOW)
        assert summary.calls_last_24h == 1
        assert summary.error_rate_percent_24h == 0.0

    async def test_a_failed_call_is_recorded_and_still_raises(self) -> None:
        class _Failing(_StubProvider):
            @override
            async def _do_complete(
                self,
                messages: list[ChatMessage],
                model: str,
                *,
                tools: list[ToolDefinition] | None = None,
                config: CompletionConfig | None = None,
            ) -> CompletionResponse:
                msg = "upstream refused"
                raise InvalidRequestError(msg)

        tracker = ProviderHealthTracker()
        clock = FakeClock(start=_HEALTH_NOW)
        provider = _Failing()
        provider.bind_health_recorder(
            outcome_recorder_for(tracker, "test-provider", clock=clock)
        )

        with pytest.raises(InvalidRequestError):
            _ = await provider.complete([_msg()], "test-basic-001")

        summary = await tracker.get_summary("test-provider", now=_HEALTH_NOW)
        assert summary.calls_last_24h == 1
        assert summary.error_rate_percent_24h == 100.0

    async def test_a_recorder_fault_does_not_fail_the_call(self) -> None:
        # The caller already has its answer; a tracker fault must not turn a
        # completed call into an error it did not have.
        async def _explodes(_outcome: CallOutcome) -> None:
            msg = "tracker exploded"
            raise RuntimeError(msg)

        provider = _StubProvider()
        provider.bind_health_recorder(_explodes)

        result = await provider.complete([_msg()], "test-basic-001")

        assert result.content == "hello"

    async def test_an_unbound_provider_records_nothing(self) -> None:
        # The tracker is wired once persistence is connected, so a driver
        # built before that must still complete.
        result = await _StubProvider().complete([_msg()], "test-basic-001")

        assert result.content == "hello"

    async def test_a_stream_setup_is_recorded(self) -> None:
        # A stream-only workload otherwise contributes no outcome at all, so
        # its provider reads as never having been called however much traffic
        # it is actually serving.
        tracker = ProviderHealthTracker()
        clock = FakeClock(start=_HEALTH_NOW)
        provider = _StubProvider()
        provider.bind_health_recorder(
            outcome_recorder_for(tracker, "test-provider", clock=clock)
        )

        stream = await provider.stream([_msg()], "test-basic-001")
        _ = [chunk async for chunk in stream]

        summary = await tracker.get_summary("test-provider", now=_HEALTH_NOW)
        assert summary.calls_last_24h == 1
        assert summary.error_rate_percent_24h == 0.0

    async def test_a_failed_stream_setup_is_recorded_as_a_failure(self) -> None:
        class _FailingStream(_StubProvider):
            @override
            async def _do_stream(
                self,
                messages: list[ChatMessage],
                model: str,
                *,
                tools: list[ToolDefinition] | None = None,
                config: CompletionConfig | None = None,
            ) -> AsyncIterator[StreamChunk]:
                msg = "upstream refused the stream"
                raise InvalidRequestError(msg)

        tracker = ProviderHealthTracker()
        clock = FakeClock(start=_HEALTH_NOW)
        provider = _FailingStream()
        provider.bind_health_recorder(
            outcome_recorder_for(tracker, "test-provider", clock=clock)
        )

        with pytest.raises(InvalidRequestError):
            _ = await provider.stream([_msg()], "test-basic-001")

        summary = await tracker.get_summary("test-provider", now=_HEALTH_NOW)
        assert summary.calls_last_24h == 1
        assert summary.error_rate_percent_24h == 100.0


@pytest.mark.unit
class TestOutcomeRecordCarriesWhatServiceabilityNeeds:
    """A provider-level verdict cannot answer "which model is failing".

    Every fact below is already in scope at the chokepoint and was simply
    not passed on: the model is a local in ``complete``, the error class is
    computed one call earlier for the Prometheus counter, and the attribution
    is read two lines later for the cost record.
    """

    async def test_a_success_records_the_model_it_called(self) -> None:
        tracker = ProviderHealthTracker()
        clock = FakeClock(start=_HEALTH_NOW)
        provider = _StubProvider()
        provider.bind_health_recorder(
            outcome_recorder_for(tracker, "test-provider", clock=clock)
        )

        _ = await provider.complete([_msg()], "test-small-001")

        view = await tracker.get_serviceability(
            "test-provider", "test-small-001", now=_HEALTH_NOW
        )
        assert view.call_count == 1
        assert view.outcome_counts[ProviderOutcomeClass.SUCCESS] == 1

    async def test_a_failure_records_its_classified_outcome(self) -> None:
        class _Overloaded(_StubProvider):
            @override
            async def _do_complete(
                self,
                messages: list[ChatMessage],
                model: str,
                *,
                tools: list[ToolDefinition] | None = None,
                config: CompletionConfig | None = None,
            ) -> CompletionResponse:
                msg = "model is temporarily overloaded"
                raise ProviderOverloadedError(msg)

        tracker = ProviderHealthTracker()
        clock = FakeClock(start=_HEALTH_NOW)
        provider = _Overloaded()
        provider.bind_health_recorder(
            outcome_recorder_for(tracker, "test-provider", clock=clock)
        )

        with pytest.raises(ProviderOverloadedError):
            _ = await provider.complete([_msg()], "test-large-001")

        view = await tracker.get_serviceability(
            "test-provider", "test-large-001", now=_HEALTH_NOW
        )
        assert view.outcome_counts[ProviderOutcomeClass.OVERLOADED] == 1

    async def test_two_models_on_one_provider_stay_separate(self) -> None:
        # The whole point: one failing model must not be averaged away by
        # its healthy siblings on the same connection, which is exactly what
        # the provider-level summary does.
        class _FailsOneModel(_StubProvider):
            @override
            async def _do_complete(
                self,
                messages: list[ChatMessage],
                model: str,
                *,
                tools: list[ToolDefinition] | None = None,
                config: CompletionConfig | None = None,
            ) -> CompletionResponse:
                if model == "test-large-001":
                    msg = "model is temporarily overloaded"
                    raise ProviderOverloadedError(msg)
                return await super()._do_complete(
                    messages, model, tools=tools, config=config
                )

        tracker = ProviderHealthTracker()
        clock = FakeClock(start=_HEALTH_NOW)
        provider = _FailsOneModel()
        provider.bind_health_recorder(
            outcome_recorder_for(tracker, "test-provider", clock=clock)
        )

        for _ in range(4):
            _ = await provider.complete([_msg()], "test-small-001")
        for _ in range(4):
            with pytest.raises(ProviderOverloadedError):
                _ = await provider.complete([_msg()], "test-large-001")

        healthy = await tracker.get_serviceability(
            "test-provider", "test-small-001", now=_HEALTH_NOW
        )
        failing = await tracker.get_serviceability(
            "test-provider", "test-large-001", now=_HEALTH_NOW
        )
        assert healthy.verdict is ProviderHealthStatus.UP
        assert failing.verdict is ProviderHealthStatus.DOWN

    async def test_attribution_comes_from_the_open_cost_scope(self) -> None:
        tracker = ProviderHealthTracker()
        clock = FakeClock(start=_HEALTH_NOW)
        provider = _StubProvider()
        provider.bind_health_recorder(
            outcome_recorder_for(tracker, "test-provider", clock=clock)
        )
        cost_tracker = CostTracker(budget_config=BudgetConfig())

        async with cost_recording_scope(
            cost_tracker=cost_tracker,
            agent_id="anica",
            task_id="task-7",
            purpose=None,
            call_category=LLMCallCategory.PRODUCTIVE,
            currency=DEFAULT_CURRENCY,
        ):
            _ = await provider.complete([_msg()], "test-small-001")

        records = await tracker.records_for_agent("anica", now=_HEALTH_NOW)
        assert len(records) == 1
        assert records[0].task_id == "task-7"
        assert records[0].model == "test-small-001"

    async def test_no_scope_means_no_attribution_not_a_placeholder(self) -> None:
        # An id naming no row would make an agent's history look complete
        # while pointing at nothing.
        tracker = ProviderHealthTracker()
        clock = FakeClock(start=_HEALTH_NOW)
        provider = _StubProvider()
        provider.bind_health_recorder(
            outcome_recorder_for(tracker, "test-provider", clock=clock)
        )

        _ = await provider.complete([_msg()], "test-small-001")

        view = await tracker.get_serviceability(
            "test-provider", "test-small-001", now=_HEALTH_NOW
        )
        assert view.call_count == 1
        assert await tracker.records_for_agent("anica", now=_HEALTH_NOW) == ()

    async def test_a_probe_is_not_evidence_about_a_model(self) -> None:
        # A probe calls no model, so it can neither prove nor disprove that
        # one serves work; letting it into the window is how a healthy ping
        # cadence diluted a failing model's error rate.
        tracker = ProviderHealthTracker()
        clock = FakeClock(start=_HEALTH_NOW)
        await record_call_outcome(
            tracker,
            "test-provider",
            CallOutcome(success=True, response_time_ms=5.0),
            clock=clock,
            source=RecordSource.PROBE,
        )

        view = await tracker.get_serviceability(
            "test-provider", "test-small-001", now=_HEALTH_NOW
        )
        assert view.call_count == 0
        assert view.verdict is ProviderHealthStatus.UNKNOWN


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

    async def test_driver_reported_cache_hit_survives_the_merge(self) -> None:
        """A driver-set _synthorg_cache_hit is additive, not clobbered.

        ``merge_call_metadata`` only injects latency/retry keys; a driver's
        own capability signal (set on its response before the base class
        ever sees it) must still be present afterwards.
        """

        class _CachingProvider(_StubProvider):
            @override
            async def _do_complete(
                self,
                messages: list[ChatMessage],
                model: str,
                *,
                tools: list[ToolDefinition] | None = None,
                config: CompletionConfig | None = None,
            ) -> CompletionResponse:
                result = await super()._do_complete(
                    messages, model, tools=tools, config=config
                )
                return result.model_copy(
                    update={"provider_metadata": {"_synthorg_cache_hit": True}}
                )

        provider = _CachingProvider()
        response = await provider.complete([_msg()], "test-model")
        assert response.provider_metadata["_synthorg_cache_hit"] is True
        assert "_synthorg_latency_ms" in response.provider_metadata

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
