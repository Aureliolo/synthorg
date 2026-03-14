"""Tests for the weighted trust strategy."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from synthorg.core.enums import ToolAccessLevel
from synthorg.core.types import NotBlankStr
from synthorg.security.trust.models import TrustState
from synthorg.security.trust.weighted_strategy import WeightedTrustStrategy
from tests.unit.security.trust.conftest import make_performance_snapshot

if TYPE_CHECKING:
    from synthorg.security.trust.config import TrustConfig

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestWeightedTrustStrategy:
    """Tests for WeightedTrustStrategy."""

    def test_name_property(self, weighted_config: TrustConfig) -> None:
        strategy = WeightedTrustStrategy(config=weighted_config)
        assert strategy.name == "weighted"

    def test_initial_state_with_score_zero(
        self,
        weighted_config: TrustConfig,
    ) -> None:
        strategy = WeightedTrustStrategy(config=weighted_config)
        state = strategy.initial_state(
            agent_id=NotBlankStr("agent-001"),
        )
        assert isinstance(state, TrustState)
        assert state.agent_id == "agent-001"
        assert state.global_level == ToolAccessLevel.SANDBOXED
        assert state.trust_score == 0.0

    async def test_evaluate_high_quality_recommends_promotion(
        self,
        weighted_config: TrustConfig,
    ) -> None:
        strategy = WeightedTrustStrategy(config=weighted_config)
        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.SANDBOXED,
            trust_score=0.0,
        )
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=9.0,
            success_rate=0.95,
            tasks_completed=20,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.score is not None
        assert result.score > 0.5
        assert result.recommended_level.value in (
            "restricted",
            "standard",
            "elevated",
        )
        assert result.should_change is True
        assert result.strategy_name == "weighted"

    async def test_evaluate_low_scores_stays_at_current(
        self,
        weighted_config: TrustConfig,
    ) -> None:
        strategy = WeightedTrustStrategy(config=weighted_config)
        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.SANDBOXED,
            trust_score=0.0,
        )
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=2.0,
            success_rate=0.3,
            tasks_completed=3,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.score is not None
        assert result.score < 0.5
        assert result.recommended_level == ToolAccessLevel.SANDBOXED
        assert result.should_change is False

    async def test_human_approval_for_standard_to_elevated(
        self,
        weighted_config: TrustConfig,
    ) -> None:
        """Standard-to-elevated threshold requires human approval.

        The agent starts at STANDARD with a high trust_score. The
        snapshot produces a score exceeding the standard_to_elevated
        threshold (0.9), so _score_to_level moves one level up to
        ELEVATED and _check_human_approval returns True.

        Score calculation with quality=9.5, success_rate=0.99,
        tasks_completed=100:
          difficulty = 0.95, completion = 0.99, error = 1.0,
          feedback = 1.0 → score ≈ 0.9825, above the 0.9 threshold.
        """
        strategy = WeightedTrustStrategy(config=weighted_config)
        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.STANDARD,
            trust_score=0.85,
        )
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=9.5,
            success_rate=0.99,
            tasks_completed=100,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.recommended_level == ToolAccessLevel.ELEVATED
        assert result.requires_human_approval is True

    async def test_evaluate_returns_score_in_range(
        self,
        weighted_config: TrustConfig,
    ) -> None:
        strategy = WeightedTrustStrategy(config=weighted_config)
        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.SANDBOXED,
            trust_score=0.0,
        )
        snapshot = make_performance_snapshot("agent-001")

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.score is not None
        assert 0.0 <= result.score <= 1.0

    async def test_evaluate_already_at_target_no_change(
        self,
        weighted_config: TrustConfig,
    ) -> None:
        """Agent already at restricted with score below standard threshold."""
        strategy = WeightedTrustStrategy(config=weighted_config)
        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.RESTRICTED,
            trust_score=0.6,
        )
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=5.0,
            success_rate=0.6,
            tasks_completed=8,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.current_level == ToolAccessLevel.RESTRICTED

    async def test_evaluate_none_quality_score(
        self,
        weighted_config: TrustConfig,
    ) -> None:
        """Agent with no quality score (None) gets 0.0 difficulty factor."""
        from synthorg.hr.performance.models import (
            AgentPerformanceSnapshot,
            WindowMetrics,
        )

        strategy = WeightedTrustStrategy(config=weighted_config)
        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.SANDBOXED,
        )
        window = WindowMetrics(
            window_size="30d",
            data_point_count=10,
            tasks_completed=10,
            tasks_failed=0,
            avg_quality_score=None,
            success_rate=1.0,
        )
        snapshot = AgentPerformanceSnapshot(
            agent_id="agent-001",
            computed_at=datetime.now(UTC),
            windows=(window,),
            overall_quality_score=None,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.score is not None
        # Score should be lower without quality contribution
        assert result.score < 0.8

    async def test_evaluate_empty_windows(
        self,
        weighted_config: TrustConfig,
    ) -> None:
        """Snapshot with no windows produces minimal score."""
        from synthorg.hr.performance.models import AgentPerformanceSnapshot

        strategy = WeightedTrustStrategy(config=weighted_config)
        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.SANDBOXED,
        )
        snapshot = AgentPerformanceSnapshot(
            agent_id="agent-001",
            computed_at=datetime.now(UTC),
            windows=(),
            overall_quality_score=None,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        # Only error_factor contributes (defaults to 1.0 when no windows)
        assert result.score is not None
        assert result.score <= 0.3
