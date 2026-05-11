"""Tests for CompositeQualityStrategy."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.composite_quality_strategy import (
    CompositeQualityStrategy,
)
from synthorg.hr.performance.models import QualityScoreResult
from synthorg.hr.performance.quality_override_store import QualityOverrideStore
from synthorg.hr.performance.quality_protocol import QualityScoringStrategy
from synthorg.providers.errors import ProviderInternalError
from synthorg.providers.resilience.errors import RetryExhaustedError

from .conftest import make_quality_override, make_task_metric

NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)


def _make_strategy(
    *,
    name: str = "test_strategy",
    score: float = 7.0,
    confidence: float = 0.8,
) -> AsyncMock:
    """Build a mock QualityScoringStrategy."""
    strategy = AsyncMock()
    strategy.name = name
    strategy.score.return_value = QualityScoreResult(
        score=score,
        strategy_name=NotBlankStr(name),
        breakdown=(("test_component", score),),
        confidence=confidence,
    )
    return strategy


def _make_failing_strategy() -> AsyncMock:
    """Build a mock strategy that returns zero confidence (failure)."""
    strategy = AsyncMock()
    strategy.name = "failing"
    strategy.score.return_value = QualityScoreResult(
        score=0.0,
        strategy_name=NotBlankStr("failing"),
        breakdown=(),
        confidence=0.0,
    )
    return strategy


@pytest.mark.unit
class TestName:
    """Strategy name property."""

    def test_name(self) -> None:
        """Composite strategy name is 'composite'."""
        ci = _make_strategy(name="ci_signal")
        composite = CompositeQualityStrategy(ci_strategy=ci)

        assert composite.name == "composite"


@pytest.mark.unit
class TestOverride:
    """Human override short-circuits scoring."""

    async def test_active_override_returns_immediately(self) -> None:
        """Active override returns override score with confidence=1.0."""
        ci = _make_strategy(name="ci_signal", score=5.0)
        store = QualityOverrideStore()
        store.set_override(make_quality_override(score=9.5, applied_at=NOW))
        composite = CompositeQualityStrategy(
            ci_strategy=ci,
            override_store=store,
        )
        record = make_task_metric(completed_at=NOW)

        result = await composite.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        assert result.score == 9.5
        assert result.confidence == 1.0
        assert result.strategy_name == "human_override"
        ci.score.assert_not_called()

    async def test_expired_override_falls_through(self) -> None:
        """Expired override does not short-circuit; CI is used."""
        ci = _make_strategy(name="ci_signal", score=6.0, confidence=0.8)
        store = QualityOverrideStore()
        store.set_override(
            make_quality_override(
                score=9.0,
                applied_at=NOW - timedelta(hours=2),
                expires_at=NOW - timedelta(hours=1),
            ),
        )
        composite = CompositeQualityStrategy(
            ci_strategy=ci,
            override_store=store,
        )
        record = make_task_metric(completed_at=NOW)

        result = await composite.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        # Should fall through to CI-only (score=6.0).
        assert result.score == 6.0
        ci.score.assert_called_once()

    async def test_no_override_store(self) -> None:
        """No override store configured -- skip override check."""
        ci = _make_strategy(name="ci_signal", score=7.0)
        composite = CompositeQualityStrategy(
            ci_strategy=ci,
            override_store=None,
        )
        record = make_task_metric(completed_at=NOW)

        result = await composite.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        ci.score.assert_called_once()
        assert result.score == 7.0


@pytest.mark.unit
class TestWeightedCombination:
    """Weighted combination of CI and LLM scores."""

    async def test_ci_plus_llm(self) -> None:
        """Both layers contribute with configured weights."""
        ci = _make_strategy(name="ci_signal", score=6.0, confidence=0.8)
        llm = _make_strategy(name="llm_judge", score=8.0, confidence=0.8)
        composite = CompositeQualityStrategy(
            ci_strategy=ci,
            llm_strategy=llm,
            ci_weight=0.4,
            llm_weight=0.6,
        )
        record = make_task_metric(completed_at=NOW)

        result = await composite.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        expected = 6.0 * 0.4 + 8.0 * 0.6  # 7.2
        assert abs(result.score - expected) < 0.01
        assert result.strategy_name == "composite"

    async def test_ci_only_no_llm_configured(self) -> None:
        """CI-only mode when no LLM strategy is provided."""
        ci = _make_strategy(name="ci_signal", score=7.5, confidence=0.8)
        composite = CompositeQualityStrategy(ci_strategy=ci)
        record = make_task_metric(completed_at=NOW)

        result = await composite.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        assert result.score == 7.5

    async def test_llm_failure_falls_back_to_ci(self) -> None:
        """LLM failure (confidence=0) falls back to CI-only."""
        ci = _make_strategy(name="ci_signal", score=7.0, confidence=0.8)
        llm = _make_failing_strategy()
        composite = CompositeQualityStrategy(
            ci_strategy=ci,
            llm_strategy=llm,
            ci_weight=0.4,
            llm_weight=0.6,
        )
        record = make_task_metric(completed_at=NOW)

        result = await composite.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        # Should use CI score directly.
        assert result.score == 7.0

    async def test_llm_exception_falls_back_to_ci(self) -> None:
        """LLM strategy raising exception falls back to CI-only."""
        ci = _make_strategy(name="ci_signal", score=7.0, confidence=0.8)
        llm = AsyncMock(spec=QualityScoringStrategy)
        llm.name = "llm_judge"
        llm.score.side_effect = RuntimeError("LLM down")
        composite = CompositeQualityStrategy(
            ci_strategy=ci,
            llm_strategy=llm,
            ci_weight=0.4,
            llm_weight=0.6,
        )
        record = make_task_metric(completed_at=NOW)

        result = await composite.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        assert result.score == 7.0

    @pytest.mark.parametrize(
        ("ci_w", "llm_w", "ci_s", "llm_s", "expected"),
        [
            (0.5, 0.5, 6.0, 8.0, 7.0),
            (0.3, 0.7, 10.0, 10.0, 10.0),
            (0.4, 0.6, 0.0, 0.0, 0.0),
            (1.0, 0.0, 5.0, 10.0, 5.0),
            (0.0, 1.0, 5.0, 10.0, 10.0),
        ],
    )
    async def test_weight_combinations(
        self,
        ci_w: float,
        llm_w: float,
        ci_s: float,
        llm_s: float,
        expected: float,
    ) -> None:
        """Various weight combinations produce correct scores."""
        ci = _make_strategy(name="ci_signal", score=ci_s, confidence=0.8)
        llm = _make_strategy(name="llm_judge", score=llm_s, confidence=0.8)
        composite = CompositeQualityStrategy(
            ci_strategy=ci,
            llm_strategy=llm,
            ci_weight=ci_w,
            llm_weight=llm_w,
        )
        record = make_task_metric(completed_at=NOW)

        result = await composite.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        assert abs(result.score - expected) < 0.01
        assert result.strategy_name == "composite"


@pytest.mark.unit
class TestBreakdown:
    """Breakdown contains individual layer scores."""

    async def test_breakdown_has_both_layers(self) -> None:
        """Breakdown includes CI and LLM layer scores."""
        ci = _make_strategy(name="ci_signal", score=6.0)
        llm = _make_strategy(name="llm_judge", score=8.0)
        composite = CompositeQualityStrategy(
            ci_strategy=ci,
            llm_strategy=llm,
        )
        record = make_task_metric(completed_at=NOW)

        result = await composite.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        breakdown_dict = dict(result.breakdown)
        assert "ci_signal" in breakdown_dict
        assert "llm_judge" in breakdown_dict
        assert breakdown_dict["ci_signal"] == 6.0
        assert breakdown_dict["llm_judge"] == 8.0

    async def test_breakdown_ci_only(self) -> None:
        """CI-only breakdown when LLM not configured."""
        ci = _make_strategy(name="ci_signal", score=7.5)
        composite = CompositeQualityStrategy(ci_strategy=ci)
        record = make_task_metric(completed_at=NOW)

        result = await composite.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        breakdown_dict = dict(result.breakdown)
        assert "ci_signal" in breakdown_dict
        assert "llm_judge" not in breakdown_dict


@pytest.mark.unit
class TestConfidence:
    """Confidence reflects which layers contributed."""

    async def test_both_layers_high_confidence(self) -> None:
        """Both layers contributing gives higher confidence."""
        ci = _make_strategy(name="ci_signal", score=7.0, confidence=0.8)
        llm = _make_strategy(name="llm_judge", score=7.0, confidence=0.8)
        composite = CompositeQualityStrategy(
            ci_strategy=ci,
            llm_strategy=llm,
        )
        record = make_task_metric(completed_at=NOW)

        both_result = await composite.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        # CI-only for comparison.
        ci_only = CompositeQualityStrategy(ci_strategy=ci)
        ci_result = await ci_only.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(),
        )

        assert both_result.confidence > ci_result.confidence


@pytest.mark.unit
class TestRetryExhaustedPropagation:
    """RetryExhaustedError propagates to engine fallback chain."""

    async def test_llm_retry_exhausted_raises(self) -> None:
        """RetryExhaustedError from LLM bubbles up unwrapped."""
        ci = _make_strategy(name="ci_signal", score=7.0, confidence=0.8)
        llm = AsyncMock(spec=QualityScoringStrategy)
        llm.name = "llm_judge"
        llm.score.side_effect = RetryExhaustedError(
            ProviderInternalError("upstream 500"),
        )
        composite = CompositeQualityStrategy(
            ci_strategy=ci,
            llm_strategy=llm,
            ci_weight=0.4,
            llm_weight=0.6,
        )
        record = make_task_metric(completed_at=NOW)

        with pytest.raises(RetryExhaustedError):
            await composite.score(
                agent_id=NotBlankStr("agent-001"),
                task_id=NotBlankStr("task-001"),
                task_result=record,
                acceptance_criteria=(),
            )

    @pytest.mark.parametrize(
        "error_cls",
        [MemoryError, RecursionError],
    )
    async def test_system_errors_propagate_unwrapped(
        self,
        error_cls: type[BaseException],
    ) -> None:
        """MemoryError and RecursionError propagate bare from TaskGroup."""
        ci = _make_strategy(name="ci_signal", score=7.0, confidence=0.8)
        llm = AsyncMock(spec=QualityScoringStrategy)
        llm.name = "llm_judge"
        llm.score.side_effect = error_cls("boom")
        composite = CompositeQualityStrategy(
            ci_strategy=ci,
            llm_strategy=llm,
            ci_weight=0.4,
            llm_weight=0.6,
        )
        record = make_task_metric(completed_at=NOW)

        with pytest.raises(error_cls):
            await composite.score(
                agent_id=NotBlankStr("agent-001"),
                task_id=NotBlankStr("task-001"),
                task_result=record,
                acceptance_criteria=(),
            )


@pytest.mark.unit
class TestWeightValidation:
    """Constructor validates weights and discount parameters."""

    def test_negative_weight_raises(self) -> None:
        """Negative weights are rejected."""
        ci = _make_strategy()
        with pytest.raises(ValueError, match="non-negative"):
            CompositeQualityStrategy(
                ci_strategy=ci,
                ci_weight=-0.1,
                llm_weight=1.1,
            )

    def test_nan_weight_raises(self) -> None:
        """NaN weights are rejected."""
        ci = _make_strategy()
        with pytest.raises(ValueError, match="finite"):
            CompositeQualityStrategy(
                ci_strategy=ci,
                ci_weight=float("nan"),
                llm_weight=0.5,
            )

    def test_weights_not_summing_raises(self) -> None:
        """Weights not summing to 1.0 are rejected."""
        ci = _make_strategy()
        with pytest.raises(ValueError, match=r"1\.0"):
            CompositeQualityStrategy(
                ci_strategy=ci,
                ci_weight=0.3,
                llm_weight=0.3,
            )

    def test_confidence_discount_nan_raises(self) -> None:
        """NaN confidence_discount is rejected."""
        ci = _make_strategy()
        with pytest.raises(ValueError, match="confidence_discount"):
            CompositeQualityStrategy(
                ci_strategy=ci,
                confidence_discount=float("nan"),
            )

    def test_confidence_discount_negative_raises(self) -> None:
        """Negative confidence_discount is rejected."""
        ci = _make_strategy()
        with pytest.raises(ValueError, match="confidence_discount"):
            CompositeQualityStrategy(
                ci_strategy=ci,
                confidence_discount=-0.1,
            )

    def test_ci_only_confidence_discount_out_of_range_raises(self) -> None:
        """ci_only_confidence_discount > 1.0 is rejected."""
        ci = _make_strategy()
        with pytest.raises(ValueError, match="ci_only_confidence_discount"):
            CompositeQualityStrategy(
                ci_strategy=ci,
                ci_only_confidence_discount=1.5,
            )
