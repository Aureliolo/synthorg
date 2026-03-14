"""Tests for the milestone trust strategy."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.enums import ToolAccessLevel
from synthorg.core.types import NotBlankStr
from synthorg.security.trust.config import (
    MilestoneCriteria,
    ReVerificationConfig,
    TrustConfig,
)
from synthorg.security.trust.enums import TrustStrategyType
from synthorg.security.trust.milestone_strategy import MilestoneTrustStrategy
from synthorg.security.trust.models import TrustState
from tests.unit.security.trust.conftest import make_performance_snapshot

pytestmark = pytest.mark.timeout(30)

_NOW = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC)

# Minimal milestone to satisfy non-empty milestones validation
_MINIMAL_MILESTONES = {
    "sandboxed_to_restricted": MilestoneCriteria(
        tasks_completed=100,
        quality_score_min=9.0,
    ),
}


@pytest.mark.unit
class TestMilestoneTrustStrategy:
    """Tests for MilestoneTrustStrategy."""

    def test_name_property(self, milestone_config: TrustConfig) -> None:
        strategy = MilestoneTrustStrategy(config=milestone_config)
        assert strategy.name == "milestone"

    def test_initial_state_with_empty_milestone_progress(
        self,
        milestone_config: TrustConfig,
    ) -> None:
        strategy = MilestoneTrustStrategy(config=milestone_config)
        state = strategy.initial_state(
            agent_id=NotBlankStr("agent-001"),
        )

        assert isinstance(state, TrustState)
        assert state.agent_id == "agent-001"
        assert state.global_level == ToolAccessLevel.SANDBOXED
        assert state.milestone_progress == {}

    async def test_evaluate_promotes_when_milestones_met(
        self,
        milestone_config: TrustConfig,
    ) -> None:
        """Agent at SANDBOXED with enough tasks/quality should promote."""
        strategy = MilestoneTrustStrategy(config=milestone_config)
        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.SANDBOXED,
        )
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=8.0,
            success_rate=0.95,
            tasks_completed=10,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.recommended_level == ToolAccessLevel.RESTRICTED
        assert result.should_change is True
        assert result.strategy_name == "milestone"

    async def test_evaluate_no_change_when_criteria_not_met(
        self,
        milestone_config: TrustConfig,
    ) -> None:
        """Agent without enough tasks stays at current level."""
        strategy = MilestoneTrustStrategy(config=milestone_config)
        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.SANDBOXED,
        )
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=5.0,
            success_rate=0.8,
            tasks_completed=2,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.recommended_level == ToolAccessLevel.SANDBOXED
        assert result.should_change is False

    async def test_decay_when_idle_too_long(self) -> None:
        """Agent idle beyond decay threshold should be demoted."""
        config = TrustConfig(
            strategy=TrustStrategyType.MILESTONE,
            initial_level=ToolAccessLevel.SANDBOXED,
            milestones=_MINIMAL_MILESTONES,
            re_verification=ReVerificationConfig(
                enabled=True,
                interval_days=90,
                decay_on_idle_days=30,
                decay_on_error_rate=0.15,
            ),
        )
        strategy = MilestoneTrustStrategy(config=config)

        # Agent at RESTRICTED, last evaluated 45 days ago
        last_eval = _NOW - timedelta(days=45)
        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.RESTRICTED,
            last_evaluated_at=last_eval,
        )
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=8.0,
            success_rate=0.95,
            tasks_completed=20,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.recommended_level == ToolAccessLevel.SANDBOXED
        assert result.should_change is True

    async def test_decay_when_error_rate_exceeds_threshold(self) -> None:
        """High error rate triggers trust decay."""
        config = TrustConfig(
            strategy=TrustStrategyType.MILESTONE,
            initial_level=ToolAccessLevel.SANDBOXED,
            milestones=_MINIMAL_MILESTONES,
            re_verification=ReVerificationConfig(
                enabled=True,
                interval_days=90,
                decay_on_idle_days=30,
                decay_on_error_rate=0.15,
            ),
        )
        strategy = MilestoneTrustStrategy(config=config)

        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.STANDARD,
            last_evaluated_at=_NOW - timedelta(days=1),
        )
        # success_rate=0.80 -> error_rate=0.20 > 0.15 threshold
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=7.0,
            success_rate=0.80,
            tasks_completed=16,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.recommended_level == ToolAccessLevel.RESTRICTED
        assert result.should_change is True

    async def test_no_decay_when_re_verification_disabled(self) -> None:
        """Decay is skipped when re-verification is disabled."""
        config = TrustConfig(
            strategy=TrustStrategyType.MILESTONE,
            initial_level=ToolAccessLevel.SANDBOXED,
            milestones=_MINIMAL_MILESTONES,
            re_verification=ReVerificationConfig(
                enabled=False,
            ),
        )
        strategy = MilestoneTrustStrategy(config=config)

        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.RESTRICTED,
            last_evaluated_at=_NOW - timedelta(days=100),
        )
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=3.0,
            success_rate=0.5,
            tasks_completed=5,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.recommended_level == ToolAccessLevel.RESTRICTED
        assert result.should_change is False

    async def test_no_decay_at_sandboxed(self) -> None:
        """Cannot decay below SANDBOXED (rank 0)."""
        config = TrustConfig(
            strategy=TrustStrategyType.MILESTONE,
            initial_level=ToolAccessLevel.SANDBOXED,
            milestones={
                "sandboxed_to_restricted": MilestoneCriteria(
                    tasks_completed=100,
                    quality_score_min=9.0,
                ),
            },
            re_verification=ReVerificationConfig(
                enabled=True,
                decay_on_idle_days=1,
            ),
        )
        strategy = MilestoneTrustStrategy(config=config)

        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.SANDBOXED,
            last_evaluated_at=_NOW - timedelta(days=100),
        )
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=3.0,
            success_rate=0.5,
            tasks_completed=5,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.recommended_level == ToolAccessLevel.SANDBOXED
        assert result.should_change is False

    async def test_milestone_requires_human_for_elevated(
        self,
        milestone_config: TrustConfig,
    ) -> None:
        """standard_to_elevated milestone has requires_human_approval=True.

        The agent is at STANDARD level. The milestone_config defines
        standard_to_elevated with tasks_completed=30, quality_score_min=8.0,
        and requires_human_approval=True. With 50 tasks and quality 9.0,
        the criteria are met and the strategy should recommend ELEVATED
        with human approval required.
        """
        strategy = MilestoneTrustStrategy(config=milestone_config)
        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.STANDARD,
        )
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=9.0,
            success_rate=1.0,
            tasks_completed=50,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.recommended_level == ToolAccessLevel.ELEVATED
        assert result.requires_human_approval is True

    async def test_milestone_quality_below_threshold_no_promote(
        self,
    ) -> None:
        """Low quality prevents milestone promotion."""
        config = TrustConfig(
            strategy=TrustStrategyType.MILESTONE,
            initial_level=ToolAccessLevel.SANDBOXED,
            milestones={
                "sandboxed_to_restricted": MilestoneCriteria(
                    tasks_completed=5,
                    quality_score_min=7.0,
                ),
            },
        )
        strategy = MilestoneTrustStrategy(config=config)
        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.SANDBOXED,
        )
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=5.0,
            success_rate=0.9,
            tasks_completed=10,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.recommended_level == ToolAccessLevel.SANDBOXED
        assert result.should_change is False

    async def test_time_active_days_not_met_blocks_promotion(self) -> None:
        """Milestone requires time_active_days, agent not active long enough."""
        config = TrustConfig(
            strategy=TrustStrategyType.MILESTONE,
            initial_level=ToolAccessLevel.SANDBOXED,
            milestones={
                "sandboxed_to_restricted": MilestoneCriteria(
                    tasks_completed=5,
                    quality_score_min=6.0,
                    time_active_days=30,
                ),
            },
        )
        strategy = MilestoneTrustStrategy(config=config)

        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.SANDBOXED,
            last_evaluated_at=_NOW - timedelta(days=5),
        )
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=9.0,
            success_rate=0.99,
            tasks_completed=100,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.recommended_level == ToolAccessLevel.SANDBOXED
        assert result.should_change is False

    async def test_time_active_days_none_last_evaluated_blocks(self) -> None:
        """time_active_days > 0 with last_evaluated_at=None blocks."""
        config = TrustConfig(
            strategy=TrustStrategyType.MILESTONE,
            initial_level=ToolAccessLevel.SANDBOXED,
            milestones={
                "sandboxed_to_restricted": MilestoneCriteria(
                    tasks_completed=5,
                    quality_score_min=6.0,
                    time_active_days=7,
                ),
            },
        )
        strategy = MilestoneTrustStrategy(config=config)

        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.SANDBOXED,
            last_evaluated_at=None,
        )
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=9.0,
            success_rate=0.99,
            tasks_completed=100,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.recommended_level == ToolAccessLevel.SANDBOXED
        assert result.should_change is False

    async def test_clean_history_days_blocks_with_failures(self) -> None:
        """clean_history_days blocks promotion if failures within the window."""
        config = TrustConfig(
            strategy=TrustStrategyType.MILESTONE,
            initial_level=ToolAccessLevel.SANDBOXED,
            milestones={
                "sandboxed_to_restricted": MilestoneCriteria(
                    tasks_completed=5,
                    quality_score_min=6.0,
                    clean_history_days=30,
                ),
            },
        )
        strategy = MilestoneTrustStrategy(config=config)

        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.SANDBOXED,
        )
        # Snapshot uses 30d window (matches clean_history_days=30)
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=9.0,
            success_rate=0.9,
            tasks_completed=10,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.recommended_level == ToolAccessLevel.SANDBOXED
        assert result.should_change is False

    async def test_re_verification_interval_decay(self) -> None:
        """Re-verification interval decay when quality is low."""
        config = TrustConfig(
            strategy=TrustStrategyType.MILESTONE,
            initial_level=ToolAccessLevel.SANDBOXED,
            milestones=_MINIMAL_MILESTONES,
            re_verification=ReVerificationConfig(
                enabled=True,
                interval_days=30,
                decay_on_idle_days=365,
                decay_on_error_rate=0.99,
            ),
        )
        strategy = MilestoneTrustStrategy(config=config)

        # last_decay_check_at was 60 days ago (>30), quality below 7.0
        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.RESTRICTED,
            last_evaluated_at=_NOW - timedelta(days=1),
            last_decay_check_at=_NOW - timedelta(days=60),
        )
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=5.0,
            success_rate=0.95,
            tasks_completed=20,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.recommended_level == ToolAccessLevel.SANDBOXED
        assert result.should_change is True
