"""Conformance tests for the BaseCompletionProvider cost-recording chokepoint.

A successful ``provider.complete()`` call inside an open
``cost_recording_scope`` MUST emit exactly one CostRecord on the
configured ``CostTracker``. Calls outside any scope MUST NOT record.

The test parametrises the provider type so any future
``BaseCompletionProvider`` subclass added to ``synthorg.providers`` is
exercised through the chokepoint via the same minimal stub harness.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import DEFAULT_CURRENCY, CurrencyCode
from synthorg.budget.tracker import CostTracker
from synthorg.core.types import NotBlankStr
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.cost_recording import (
    cost_recording_scope,
    current_cost_context,
    resolve_currency,
)
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolDefinition,
)


class _StubProvider(BaseCompletionProvider):
    """Minimal concrete provider that returns a deterministic response."""

    def __init__(
        self,
        *,
        usage: TokenUsage | None = None,
        finish_reason: FinishReason = FinishReason.STOP,
    ) -> None:
        super().__init__()
        self._usage = usage or TokenUsage(
            input_tokens=10,
            output_tokens=5,
            cost=0.001,
        )
        self._finish_reason = finish_reason

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
            finish_reason=self._finish_reason,
            usage=self._usage,
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
            yield

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
class TestCostRecordingChokepoint:
    """Chokepoint conformance: every successful complete() inside a
    scope emits exactly one CostRecord."""

    async def test_records_cost_inside_scope(self) -> None:
        provider = _StubProvider()
        tracker = CostTracker()
        async with cost_recording_scope(
            cost_tracker=tracker,
            agent_id=NotBlankStr("agent-1"),
            task_id=NotBlankStr("task-1"),
            project_id=NotBlankStr("proj-1"),
            call_category=LLMCallCategory.SYSTEM,
            currency=CurrencyCode(DEFAULT_CURRENCY),
        ):
            response = await provider.complete([_msg()], "test-model")

        records = await tracker.get_records()
        assert len(records) == 1
        record = records[0]
        assert isinstance(record, CostRecord)
        assert record.agent_id == "agent-1"
        assert record.task_id == "task-1"
        assert record.project_id == "proj-1"
        assert record.model == "test-model"
        assert record.input_tokens == 10
        assert record.output_tokens == 5
        assert record.cost == pytest.approx(0.001)
        assert record.currency == DEFAULT_CURRENCY
        assert record.call_category == LLMCallCategory.SYSTEM
        assert record.finish_reason == FinishReason.STOP
        assert record.success is True
        assert response.usage.cost == pytest.approx(0.001)

    async def test_no_record_when_scope_not_open(self) -> None:
        provider = _StubProvider()
        tracker = CostTracker()
        await provider.complete([_msg()], "test-model")
        records = await tracker.get_records()
        assert records == ()

    async def test_no_record_for_zero_usage(self) -> None:
        provider = _StubProvider(
            usage=TokenUsage(input_tokens=0, output_tokens=0, cost=0.0),
        )
        tracker = CostTracker()
        async with cost_recording_scope(
            cost_tracker=tracker,
            agent_id=NotBlankStr("agent-1"),
            task_id=NotBlankStr("task-1"),
            call_category=LLMCallCategory.SYSTEM,
            currency=CurrencyCode(DEFAULT_CURRENCY),
        ):
            await provider.complete([_msg()], "test-model")
        records = await tracker.get_records()
        assert records == ()

    async def test_records_free_tier_with_nonzero_tokens(self) -> None:
        # Free-tier provider: zero cost but non-zero tokens. Must record.
        provider = _StubProvider(
            usage=TokenUsage(input_tokens=20, output_tokens=10, cost=0.0),
        )
        tracker = CostTracker()
        async with cost_recording_scope(
            cost_tracker=tracker,
            agent_id=NotBlankStr("agent-1"),
            task_id=NotBlankStr("task-1"),
            call_category=LLMCallCategory.PRODUCTIVE,
            currency=CurrencyCode(DEFAULT_CURRENCY),
        ):
            await provider.complete([_msg()], "test-model")
        records = await tracker.get_records()
        assert len(records) == 1
        assert records[0].cost == 0.0
        assert records[0].input_tokens == 20
        assert records[0].output_tokens == 10

    async def test_tracker_record_failure_does_not_break_complete(self) -> None:
        class _RaisingTracker(CostTracker):
            async def record(self, cost_record: CostRecord) -> None:
                _ = cost_record
                msg = "boom"
                raise RuntimeError(msg)

        provider = _StubProvider()
        tracker = _RaisingTracker()
        async with cost_recording_scope(
            cost_tracker=tracker,
            agent_id=NotBlankStr("agent-1"),
            task_id=NotBlankStr("task-1"),
            call_category=LLMCallCategory.SYSTEM,
            currency=CurrencyCode(DEFAULT_CURRENCY),
        ):
            response = await provider.complete([_msg()], "test-model")
        # Provider call still returned the response despite recording failure
        assert response.content == "hello"

    async def test_scope_resets_on_exit(self) -> None:
        tracker = CostTracker()
        assert current_cost_context() is None
        async with cost_recording_scope(
            cost_tracker=tracker,
            agent_id=NotBlankStr("agent-1"),
            task_id=NotBlankStr("task-1"),
            call_category=LLMCallCategory.SYSTEM,
            currency=CurrencyCode(DEFAULT_CURRENCY),
        ):
            assert current_cost_context() is not None
        assert current_cost_context() is None

    async def test_nested_scope_shadows_outer(self) -> None:
        tracker_outer = CostTracker()
        tracker_inner = CostTracker()
        async with cost_recording_scope(
            cost_tracker=tracker_outer,
            agent_id=NotBlankStr("outer-agent"),
            task_id=NotBlankStr("outer-task"),
            call_category=LLMCallCategory.PRODUCTIVE,
            currency=CurrencyCode(DEFAULT_CURRENCY),
        ):
            outer_ctx = current_cost_context()
            assert outer_ctx is not None
            assert outer_ctx.agent_id == "outer-agent"

            async with cost_recording_scope(
                cost_tracker=tracker_inner,
                agent_id=NotBlankStr("inner-agent"),
                task_id=NotBlankStr("inner-task"),
                call_category=LLMCallCategory.SYSTEM,
                currency=CurrencyCode(DEFAULT_CURRENCY),
            ):
                inner_ctx = current_cost_context()
                assert inner_ctx is not None
                assert inner_ctx.agent_id == "inner-agent"

            restored = current_cost_context()
            assert restored is not None
            assert restored.agent_id == "outer-agent"

    async def test_concurrent_tasks_have_independent_scopes(self) -> None:
        # contextvars propagate per asyncio.Task, so two parallel tasks
        # MUST see each other's scopes as None.
        provider = _StubProvider()
        tracker_a = CostTracker()
        tracker_b = CostTracker()
        ready_b = asyncio.Event()
        seen_in_b: list[object] = []

        async def task_b() -> None:
            await ready_b.wait()
            seen_in_b.append(current_cost_context())
            await provider.complete([_msg()], "test-model-b")

        async def task_a() -> None:
            async with cost_recording_scope(
                cost_tracker=tracker_a,
                agent_id=NotBlankStr("agent-a"),
                task_id=NotBlankStr("task-a"),
                call_category=LLMCallCategory.SYSTEM,
                currency=CurrencyCode(DEFAULT_CURRENCY),
            ):
                ready_b.set()
                await provider.complete([_msg()], "test-model-a")
                await asyncio.sleep(0)  # let task_b run

        async with asyncio.TaskGroup() as tg:
            tg.create_task(task_a())
            tg.create_task(task_b())

        # task_b ran outside any scope -> nothing recorded on tracker_b
        records_a = await tracker_a.get_records()
        records_b = await tracker_b.get_records()
        assert len(records_a) == 1
        assert records_a[0].agent_id == "agent-a"
        assert records_b == ()
        assert seen_in_b == [None]


@pytest.mark.unit
class TestStreamingBypassesChokepoint:
    """Documented limitation: ``stream()`` does not fire the chokepoint.

    Streaming responses surface usage as a terminal
    ``StreamEventType.USAGE`` chunk, so the chokepoint cannot inspect
    ``response.usage`` synchronously.  Instead of half-implementing
    cost recording for streams (which would require consuming the
    iterator inside the chokepoint and conflating recording with the
    stream-consumption contract), we explicitly leave streaming
    out-of-scope for #1598.  This test pins the contract so a future
    PR that adds streaming-aware recording must update the assertion.

    No call site in the current codebase uses ``stream()`` for paid
    LLM work; all 23 cost-attributable LLM call sites use ``complete()``.
    """

    async def test_stream_inside_scope_does_not_record(self) -> None:
        provider = _StubProvider()
        tracker = CostTracker()
        async with cost_recording_scope(
            cost_tracker=tracker,
            agent_id=NotBlankStr("agent-1"),
            task_id=NotBlankStr("task-1"),
            call_category=LLMCallCategory.SYSTEM,
            currency=CurrencyCode(DEFAULT_CURRENCY),
        ):
            stream = await provider.stream([_msg()], "test-model")
            # Drain the stream so resilience cleanup runs.
            async for _ in stream:
                pass
        # The chokepoint fires only inside complete(), never inside
        # stream(). When this assertion changes, update the docstring
        # in BaseCompletionProvider.stream and the migration plan.
        assert await tracker.get_records() == ()


@pytest.mark.unit
class TestResolveCurrency:
    async def test_resolves_from_budget_config(self) -> None:
        from synthorg.budget.config import BudgetConfig

        cfg = BudgetConfig(total_monthly=100.0, currency="EUR")
        tracker = CostTracker(budget_config=cfg)
        assert resolve_currency(tracker) == "EUR"

    async def test_falls_back_to_default(self) -> None:
        tracker = CostTracker()
        assert resolve_currency(tracker) == DEFAULT_CURRENCY


@pytest.mark.unit
class TestContextValidation:
    async def test_rejects_non_tracker(self) -> None:
        with pytest.raises(TypeError, match="cost_tracker must be a CostTracker"):
            async with cost_recording_scope(
                cost_tracker="not-a-tracker",  # type: ignore[arg-type]
                agent_id=NotBlankStr("agent-1"),
                task_id=NotBlankStr("task-1"),
                call_category=LLMCallCategory.SYSTEM,
                currency=CurrencyCode(DEFAULT_CURRENCY),
            ):
                pass
