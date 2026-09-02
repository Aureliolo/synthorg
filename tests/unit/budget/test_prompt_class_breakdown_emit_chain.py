"""End-to-end emit -> analytics chain for prompt-purpose attribution.

Drives the real cost-recording chokepoint: opens a ``cost_recording_scope``
with a ``purpose``, emits a :class:`CostRecord` through
``emit_cost_record_from_context`` exactly as ``BaseCompletionProvider.complete``
does, drains the tracker, then reads the record back through
``CallAnalyticsService.get_prompt_class_breakdown``. This proves the full
emit -> tracker -> analytics -> dashboard-DTO span end-to-end in one chain.
"""

from typing import Final

import pytest

from synthorg.budget.call_analytics import CallAnalyticsService
from synthorg.budget.call_analytics_config import CallAnalyticsConfig
from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker import CostTracker
from synthorg.core.completion_enums import FinishReason
from synthorg.llm.model_capability_policy import capability_for_purpose
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.providers.cost_recording import (
    cost_recording_scope,
    current_cost_context,
    emit_cost_record_from_context,
)
from synthorg.providers.models import CompletionResponse, TokenUsage

pytestmark = pytest.mark.unit

_LATENCY_MS: Final[float] = 123.5
_INPUT_TOKENS: Final[int] = 1000
_OUTPUT_TOKENS: Final[int] = 200
_COST: Final[float] = 0.04
_CURRENCY: Final[str] = "EUR"
_MODEL: Final[str] = "example-basic-001"
_PROVIDER: Final[str] = "test-provider"


def _response() -> CompletionResponse:
    """A successful completion carrying usage and ``_synthorg_*`` telemetry."""
    return CompletionResponse(
        content="ok",
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(
            input_tokens=_INPUT_TOKENS,
            output_tokens=_OUTPUT_TOKENS,
            cost=_COST,
            cache_read_input_tokens=_INPUT_TOKENS,
        ),
        model=_MODEL,
        provider_metadata={
            "_synthorg_latency_ms": _LATENCY_MS,
            "_synthorg_retry_count": 0,
        },
    )


async def _emit(tracker: CostTracker, purpose: PromptPurposeId | None) -> None:
    """Open a scope with ``purpose`` and emit one record, then drain."""
    async with cost_recording_scope(
        cost_tracker=tracker,
        agent_id="agent-1",
        task_id="task-1",
        purpose=purpose,
        call_category=LLMCallCategory.PRODUCTIVE,
        currency=_CURRENCY,
    ):
        ctx = current_cost_context()
        assert ctx is not None
        await emit_cost_record_from_context(
            ctx, _response(), model=_MODEL, provider=_PROVIDER
        )
    await tracker.drain_pending_records()


async def test_purpose_emit_surfaces_in_breakdown() -> None:
    tracker = CostTracker()
    await _emit(tracker, PromptPurposeId.MEMORY_RERANK)

    service = CallAnalyticsService(cost_tracker=tracker, config=CallAnalyticsConfig())
    breakdown = await service.get_prompt_class_breakdown()

    assert len(breakdown.rows) == 1
    row = breakdown.rows[0]
    assert row.prompt_class_id == PromptPurposeId.MEMORY_RERANK
    # The dashboard tier column is the same design tier the pin records.
    assert row.capability == capability_for_purpose(PromptPurposeId.MEMORY_RERANK)
    assert row.total_cost == pytest.approx(_COST)
    assert row.currency == _CURRENCY
    assert row.call_count == 1
    assert row.input_tokens == _INPUT_TOKENS
    assert row.output_tokens == _OUTPUT_TOKENS
    assert row.avg_latency_ms == pytest.approx(_LATENCY_MS)
    assert row.p95_latency_ms == pytest.approx(_LATENCY_MS)
    assert row.cached_input_share == pytest.approx(1.0)


async def test_multiple_purposes_grouped_with_promptless_bucket() -> None:
    tracker = CostTracker()
    await _emit(tracker, PromptPurposeId.MEMORY_RERANK)
    await _emit(tracker, PromptPurposeId.COS_CHAT)
    # A call wrapping no system prompt carries no prompt_class_id. It gets its
    # own bucket rather than being dropped, so the breakdown keeps summing to
    # the headline total.
    await _emit(tracker, None)

    service = CallAnalyticsService(cost_tracker=tracker, config=CallAnalyticsConfig())
    breakdown = await service.get_prompt_class_breakdown()

    ids = [row.prompt_class_id for row in breakdown.rows]
    # The promptless bucket sorts first; the rest by prompt_class_id value.
    assert ids == [None, PromptPurposeId.COS_CHAT, PromptPurposeId.MEMORY_RERANK]
    assert sum(row.total_cost for row in breakdown.rows) == pytest.approx(_COST * 3)


async def test_promptless_only_still_yields_its_bucket() -> None:
    tracker = CostTracker()
    await _emit(tracker, None)

    service = CallAnalyticsService(cost_tracker=tracker, config=CallAnalyticsConfig())
    breakdown = await service.get_prompt_class_breakdown()

    assert len(breakdown.rows) == 1
    assert breakdown.rows[0].prompt_class_id is None
    assert breakdown.rows[0].capability is None
    assert breakdown.rows[0].total_cost == pytest.approx(_COST)
