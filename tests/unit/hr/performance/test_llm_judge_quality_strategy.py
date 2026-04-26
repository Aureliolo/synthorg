"""Tests for LlmJudgeQualityStrategy."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.llm_judge_quality_strategy import (
    LlmJudgeQualityStrategy,
)
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.cost_recording import drain_pending_cost_records
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import CompletionResponse, TokenUsage

from .conftest import make_acceptance_criterion, make_task_metric

if TYPE_CHECKING:
    from synthorg.providers.capabilities import ModelCapabilities
    from synthorg.providers.models import (
        ChatMessage,
        CompletionConfig,
        StreamChunk,
        ToolDefinition,
    )

NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)


class _ChokepointStubProvider(BaseCompletionProvider):
    """Concrete BaseCompletionProvider so the cost chokepoint fires.

    Records each call into ``complete_calls`` so tests can introspect
    the prompt (replacing the legacy ``MagicMock.call_args`` pattern).
    """

    def __init__(self, response: CompletionResponse) -> None:
        super().__init__()
        self._response = response
        self.complete_calls: list[
            tuple[
                tuple[ChatMessage, ...],
                str,
                tuple[ToolDefinition, ...] | None,
                CompletionConfig | None,
            ]
        ] = []

    async def _do_complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        self.complete_calls.append(
            (
                tuple(messages),
                model,
                tuple(tools) if tools is not None else None,
                config,
            ),
        )
        return self._response

    async def _do_stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        async def _gen() -> AsyncIterator[StreamChunk]:
            # Empty async generator: the unconditional-False guard
            # keeps the function's coroutine-shape without producing
            # any chunks.  mypy gets the silencer.
            if False:
                yield  # type: ignore[unreachable]

        return _gen()

    async def _do_get_model_capabilities(
        self,
        model: str,
    ) -> ModelCapabilities:
        msg = "not implemented"
        raise NotImplementedError(msg)


def _make_provider(
    *,
    content: str = '{"score": 7.5, "rationale": "Good quality output"}',
    cost: float = 0.001,
    input_tokens: int = 200,
    output_tokens: int = 50,
) -> _ChokepointStubProvider:
    """Build a BaseCompletionProvider stub returning the given content.

    Subclasses ``BaseCompletionProvider`` so the cost-recording
    chokepoint fires inside ``complete()`` whenever a
    ``cost_recording_scope`` is open in the calling task.
    """
    return _ChokepointStubProvider(
        CompletionResponse(
            content=content,
            tool_calls=(),
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=cost,
            ),
            model=NotBlankStr("test-small-001"),
        ),
    )


@pytest.mark.unit
class TestName:
    """Strategy name property."""

    def test_name(self) -> None:
        """Strategy name is 'llm_judge'."""
        strategy = LlmJudgeQualityStrategy(
            provider=_make_provider(),
            model=NotBlankStr("test-small-001"),
        )

        assert strategy.name == "llm_judge"


@pytest.mark.unit
class TestScoring:
    """Successful scoring via LLM judge."""

    async def test_successful_scoring(self) -> None:
        """LLM returns valid JSON and score is used."""
        provider = _make_provider(
            content='{"score": 8.5, "rationale": "All criteria met well"}',
        )
        strategy = LlmJudgeQualityStrategy(
            provider=provider,
            model=NotBlankStr("test-small-001"),
        )
        record = make_task_metric(completed_at=NOW)
        criteria = (
            make_acceptance_criterion(description="Tests pass", met=True),
            make_acceptance_criterion(description="Code reviewed", met=True),
        )

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=criteria,
        )

        assert result.score == 8.5
        assert result.strategy_name == "llm_judge"
        assert result.confidence == 0.8

    async def test_empty_criteria(self) -> None:
        """Scoring works with no acceptance criteria (lower confidence)."""
        provider = _make_provider(
            content='{"score": 6.0, "rationale": "No criteria to evaluate"}',
        )
        strategy = LlmJudgeQualityStrategy(
            provider=provider,
            model=NotBlankStr("test-small-001"),
        )
        record = make_task_metric(completed_at=NOW)

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        assert result.score == 6.0
        assert result.confidence == 0.5

    async def test_criteria_present_higher_confidence_than_empty(self) -> None:
        """Criteria present -> higher confidence than empty."""
        provider = _make_provider()
        strategy = LlmJudgeQualityStrategy(
            provider=provider,
            model=NotBlankStr("test-small-001"),
        )
        record = make_task_metric(completed_at=NOW)
        criteria = (make_acceptance_criterion(),)

        with_criteria = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=criteria,
        )
        without_criteria = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        assert with_criteria.confidence > without_criteria.confidence

    async def test_score_clamped_to_range(self) -> None:
        """LLM score outside [0, 10] is clamped."""
        provider = _make_provider(
            content='{"score": 12.0, "rationale": "Very generous LLM"}',
        )
        strategy = LlmJudgeQualityStrategy(
            provider=provider,
            model=NotBlankStr("test-small-001"),
        )
        record = make_task_metric(completed_at=NOW)

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        assert result.score == 10.0

    async def test_negative_score_clamped(self) -> None:
        """Negative LLM score is clamped to 0."""
        provider = _make_provider(
            content='{"score": -3.0, "rationale": "Very harsh LLM"}',
        )
        strategy = LlmJudgeQualityStrategy(
            provider=provider,
            model=NotBlankStr("test-small-001"),
        )
        record = make_task_metric(completed_at=NOW)

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        assert result.score == 0.0

    async def test_breakdown_contains_llm_score(self) -> None:
        """Breakdown includes the LLM score component."""
        provider = _make_provider(
            content='{"score": 7.0, "rationale": "Solid work overall"}',
        )
        strategy = LlmJudgeQualityStrategy(
            provider=provider,
            model=NotBlankStr("test-small-001"),
        )
        record = make_task_metric(completed_at=NOW)

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        breakdown_dict = dict(result.breakdown)
        assert "llm_score" in breakdown_dict


@pytest.mark.unit
class TestErrorHandling:
    """Error handling and graceful degradation."""

    async def test_malformed_json(self) -> None:
        """Malformed JSON returns confidence=0.0 fallback."""
        provider = _make_provider(content="not json at all")
        strategy = LlmJudgeQualityStrategy(
            provider=provider,
            model=NotBlankStr("test-small-001"),
        )
        record = make_task_metric(completed_at=NOW)

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        assert result.confidence == 0.0
        assert result.strategy_name == "llm_judge"

    async def test_missing_score_key(self) -> None:
        """JSON without 'score' key returns confidence=0.0 fallback."""
        provider = _make_provider(content='{"rationale": "oops no score"}')
        strategy = LlmJudgeQualityStrategy(
            provider=provider,
            model=NotBlankStr("test-small-001"),
        )
        record = make_task_metric(completed_at=NOW)

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        assert result.confidence == 0.0

    async def test_blank_rationale(self) -> None:
        """Blank rationale returns confidence=0.0 fallback."""
        provider = _make_provider(content='{"score": 7.0, "rationale": ""}')
        strategy = LlmJudgeQualityStrategy(
            provider=provider,
            model=NotBlankStr("test-small-001"),
        )
        record = make_task_metric(completed_at=NOW)

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        assert result.confidence == 0.0

    async def test_provider_exception(self) -> None:
        """Provider exception returns confidence=0.0 fallback."""
        provider = AsyncMock()
        provider.complete.side_effect = RuntimeError("Connection failed")
        strategy = LlmJudgeQualityStrategy(
            provider=provider,
            model=NotBlankStr("test-small-001"),
        )
        record = make_task_metric(completed_at=NOW)

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        assert result.confidence == 0.0

    async def test_empty_content(self) -> None:
        """LLM returning None content returns confidence=0.0 fallback."""
        provider = AsyncMock()
        provider.complete.return_value = CompletionResponse(
            content=None,
            tool_calls=(),
            finish_reason=FinishReason.ERROR,
            usage=TokenUsage(input_tokens=0, output_tokens=0, cost=0.0),
            model=NotBlankStr("test-small-001"),
        )
        strategy = LlmJudgeQualityStrategy(
            provider=provider,
            model=NotBlankStr("test-small-001"),
        )
        record = make_task_metric(completed_at=NOW)

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        assert result.confidence == 0.0


@pytest.mark.unit
class TestCostTracking:
    """Cost recording via the BaseCompletionProvider chokepoint."""

    async def test_cost_recorded_on_success(self) -> None:
        """Successful scoring emits a CostRecord through the chokepoint."""
        from synthorg.budget.tracker import CostTracker

        provider = _make_provider(cost=0.002)
        cost_tracker = CostTracker()
        strategy = LlmJudgeQualityStrategy(
            provider=provider,
            model=NotBlankStr("test-small-001"),
            cost_tracker=cost_tracker,
        )
        record = make_task_metric(completed_at=NOW)

        await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        await drain_pending_cost_records()
        records = await cost_tracker.get_records()
        assert len(records) == 1
        cost_record = records[0]
        assert cost_record.cost == 0.002
        assert cost_record.currency == DEFAULT_CURRENCY
        assert cost_record.agent_id == "agent-001"
        assert cost_record.task_id == "task-001"
        assert cost_record.model == "test-small-001"

    async def test_no_cost_recorded_on_failure(self) -> None:
        """Failed scoring does not record cost."""
        from synthorg.budget.tracker import CostTracker

        # Use a stub provider that raises -- AsyncMock is not a
        # BaseCompletionProvider so the chokepoint would not fire on it.
        class _RaisingProvider(_ChokepointStubProvider):
            async def _do_complete(  # type: ignore[override]
                self,
                messages: object,
                model: object,
                **kwargs: object,
            ) -> object:
                _ = (messages, model, kwargs)
                msg = "fail"
                raise RuntimeError(msg)

        provider = _RaisingProvider(
            CompletionResponse(
                content="",
                tool_calls=(),
                finish_reason=FinishReason.STOP,
                usage=TokenUsage(input_tokens=0, output_tokens=0, cost=0.0),
                model=NotBlankStr("test-small-001"),
            ),
        )
        cost_tracker = CostTracker()
        strategy = LlmJudgeQualityStrategy(
            provider=provider,
            model=NotBlankStr("test-small-001"),
            cost_tracker=cost_tracker,
        )
        record = make_task_metric(completed_at=NOW)

        await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        await drain_pending_cost_records()
        records = await cost_tracker.get_records()
        assert records == ()

    async def test_no_cost_tracker_is_fine(self) -> None:
        """Works without a cost tracker (cost tracking optional)."""
        provider = _make_provider()
        strategy = LlmJudgeQualityStrategy(
            provider=provider,
            model=NotBlankStr("test-small-001"),
            cost_tracker=None,
        )
        record = make_task_metric(completed_at=NOW)

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        assert result.score == 7.5


@pytest.mark.unit
class TestPromptConstruction:
    """Prompt construction for the LLM."""

    async def test_criteria_included_in_prompt(self) -> None:
        """Acceptance criteria descriptions appear in the USER prompt."""
        provider = _make_provider()
        strategy = LlmJudgeQualityStrategy(
            provider=provider,
            model=NotBlankStr("test-small-001"),
        )
        record = make_task_metric(completed_at=NOW)
        criteria = (
            make_acceptance_criterion(description="All tests pass", met=True),
            make_acceptance_criterion(description="No lint errors", met=False),
        )

        await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=criteria,
        )

        # SEC-1: criteria are routed through the USER message (fenced),
        # not the SYSTEM message which carries trusted instructions.
        messages, *_ = provider.complete_calls[-1]
        user_text = messages[1].content
        assert user_text is not None
        assert "All tests pass" in user_text
        assert "No lint errors" in user_text
        assert "[MET]" in user_text
        assert "[NOT MET]" in user_text

    async def test_delimiters_in_prompt(self) -> None:
        """SEC-1: criteria are USER-fenced and SYSTEM carries the directive."""
        provider = _make_provider()
        strategy = LlmJudgeQualityStrategy(
            provider=provider,
            model=NotBlankStr("test-small-001"),
        )
        record = make_task_metric(completed_at=NOW)
        criteria = (make_acceptance_criterion(),)

        await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=criteria,
        )

        messages, *_ = provider.complete_calls[-1]
        system_text = messages[0].content
        user_text = messages[1].content
        assert system_text is not None
        assert user_text is not None
        # USER message carries the wrapped criteria payload.
        assert "<criteria-json>" in user_text
        assert "</criteria-json>" in user_text
        # SYSTEM message carries the directive listing the tag.
        assert "criteria-json" in system_text

    async def test_braces_in_criteria_escaped(self) -> None:
        """Curly braces in criteria descriptions are escaped for str.format()."""
        provider = _make_provider()
        strategy = LlmJudgeQualityStrategy(
            provider=provider,
            model=NotBlankStr("test-small-001"),
        )
        record = make_task_metric(completed_at=NOW)
        criteria = (
            make_acceptance_criterion(
                description="Output must be {valid JSON}",
                met=True,
            ),
        )

        await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=criteria,
        )

        # Criteria payload lives in the USER message (SYSTEM is fixed).
        messages, *_ = provider.complete_calls[-1]
        user_text = messages[1].content
        assert user_text is not None
        assert "{valid JSON}" in user_text


@pytest.mark.unit
class TestCostRecordingResilience:
    """Cost recording failures do not discard valid scores."""

    async def test_cost_failure_does_not_discard_score(self) -> None:
        """If cost recording fails, the LLM score is still returned.

        The chokepoint inside ``BaseCompletionProvider.complete``
        swallows tracker failures (logs at WARNING) so the LLM call
        result is preserved.  This test exercises that contract via a
        ``CostTracker`` subclass whose ``record`` raises.
        """
        from synthorg.budget.tracker import CostTracker

        class _RaisingCostTracker(CostTracker):
            async def record(self, cost_record: object) -> None:
                _ = cost_record
                msg = "DB unavailable"
                raise RuntimeError(msg)

        provider = _make_provider(
            content='{"score": 8.0, "rationale": "Great work"}',
        )
        cost_tracker = _RaisingCostTracker()
        strategy = LlmJudgeQualityStrategy(
            provider=provider,
            model=NotBlankStr("test-small-001"),
            cost_tracker=cost_tracker,
        )
        record = make_task_metric(completed_at=NOW)

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        assert result.score == 8.0
        assert result.confidence > 0.0
