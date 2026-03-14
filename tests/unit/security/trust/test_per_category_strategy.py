"""Tests for the per-category trust strategy."""

import pytest

from synthorg.core.enums import ToolAccessLevel
from synthorg.core.types import NotBlankStr
from synthorg.security.trust.config import (
    CategoryTrustCriteria,
    TrustConfig,
)
from synthorg.security.trust.enums import TrustStrategyType
from synthorg.security.trust.models import TrustState
from synthorg.security.trust.per_category_strategy import (
    PerCategoryTrustStrategy,
)
from tests.unit.security.trust.conftest import make_performance_snapshot

pytestmark = pytest.mark.timeout(30)


def _make_per_category_config(
    *,
    initial_levels: dict[str, ToolAccessLevel] | None = None,
    criteria: dict[str, dict[str, CategoryTrustCriteria]] | None = None,
) -> TrustConfig:
    """Build a per-category TrustConfig for tests."""
    levels = initial_levels or {
        "file_system": ToolAccessLevel.SANDBOXED,
        "code_execution": ToolAccessLevel.SANDBOXED,
    }
    return TrustConfig(
        strategy=TrustStrategyType.PER_CATEGORY,
        initial_level=ToolAccessLevel.SANDBOXED,
        initial_category_levels=levels,
        category_criteria=criteria or {},
    )


@pytest.mark.unit
class TestPerCategoryTrustStrategy:
    """Tests for PerCategoryTrustStrategy."""

    def test_name_property(self) -> None:
        config = _make_per_category_config()
        strategy = PerCategoryTrustStrategy(config=config)
        assert strategy.name == "per_category"

    def test_initial_state_includes_category_levels(self) -> None:
        config = _make_per_category_config(
            initial_levels={
                "file_system": ToolAccessLevel.SANDBOXED,
                "code_execution": ToolAccessLevel.RESTRICTED,
            },
        )
        strategy = PerCategoryTrustStrategy(config=config)
        state = strategy.initial_state(
            agent_id=NotBlankStr("agent-001"),
        )

        assert isinstance(state, TrustState)
        assert state.agent_id == "agent-001"
        assert state.global_level == ToolAccessLevel.SANDBOXED
        assert state.category_levels == {
            "file_system": ToolAccessLevel.SANDBOXED,
            "code_execution": ToolAccessLevel.RESTRICTED,
        }

    async def test_evaluate_returns_minimum_across_categories(
        self,
    ) -> None:
        """Global level is derived as minimum across categories."""
        config = _make_per_category_config(
            initial_levels={
                "file_system": ToolAccessLevel.STANDARD,
                "code_execution": ToolAccessLevel.SANDBOXED,
            },
        )
        strategy = PerCategoryTrustStrategy(config=config)

        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.SANDBOXED,
            category_levels={
                "file_system": ToolAccessLevel.STANDARD,
                "code_execution": ToolAccessLevel.SANDBOXED,
            },
        )
        snapshot = make_performance_snapshot("agent-001")

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        # Minimum of STANDARD and SANDBOXED = SANDBOXED
        assert result.recommended_level == ToolAccessLevel.SANDBOXED
        assert result.strategy_name == "per_category"

    async def test_evaluate_all_categories_elevated(self) -> None:
        """When all categories are elevated, global is elevated."""
        config = _make_per_category_config(
            initial_levels={
                "file_system": ToolAccessLevel.ELEVATED,
                "code_execution": ToolAccessLevel.ELEVATED,
            },
        )
        strategy = PerCategoryTrustStrategy(config=config)

        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.SANDBOXED,
            category_levels={
                "file_system": ToolAccessLevel.ELEVATED,
                "code_execution": ToolAccessLevel.ELEVATED,
            },
        )
        snapshot = make_performance_snapshot("agent-001")

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.recommended_level == ToolAccessLevel.ELEVATED

    async def test_evaluate_no_categories_returns_current(self) -> None:
        """When no category_levels are present, return current global."""
        config = _make_per_category_config()
        strategy = PerCategoryTrustStrategy(config=config)

        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.RESTRICTED,
            category_levels={},
        )
        snapshot = make_performance_snapshot("agent-001")

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.recommended_level == ToolAccessLevel.RESTRICTED
        assert result.should_change is False

    async def test_evaluate_with_criteria_promotes_category(self) -> None:
        """Category promotion when criteria are met."""
        config = _make_per_category_config(
            initial_levels={
                "file_system": ToolAccessLevel.SANDBOXED,
            },
            criteria={
                "file_system": {
                    "sandboxed_to_restricted": CategoryTrustCriteria(
                        tasks_completed=5,
                        quality_score_min=6.0,
                    ),
                },
            },
        )
        strategy = PerCategoryTrustStrategy(config=config)

        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.SANDBOXED,
            category_levels={
                "file_system": ToolAccessLevel.SANDBOXED,
            },
        )
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=8.0,
            tasks_completed=10,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.recommended_level == ToolAccessLevel.RESTRICTED

    async def test_elevated_category_requires_human_approval(self) -> None:
        """Promotion to ELEVATED forces requires_human even without criteria flag."""
        # Use restricted_to_elevated (not checked by config elevated gate)
        config = _make_per_category_config(
            initial_levels={
                "file_system": ToolAccessLevel.RESTRICTED,
            },
            criteria={
                "file_system": {
                    "restricted_to_elevated": CategoryTrustCriteria(
                        tasks_completed=5,
                        quality_score_min=6.0,
                        requires_human_approval=False,
                    ),
                },
            },
        )
        strategy = PerCategoryTrustStrategy(config=config)

        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.RESTRICTED,
            category_levels={
                "file_system": ToolAccessLevel.RESTRICTED,
            },
        )
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=9.0,
            tasks_completed=20,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        # Runtime defense-in-depth: ELEVATED always forces human approval
        assert result.requires_human_approval is True

    async def test_quality_none_with_min_zero_passes(self) -> None:
        """quality=None is accepted when quality_score_min is 0.0."""
        from datetime import UTC, datetime

        from synthorg.hr.performance.models import (
            AgentPerformanceSnapshot,
            WindowMetrics,
        )

        config = _make_per_category_config(
            initial_levels={
                "file_system": ToolAccessLevel.SANDBOXED,
            },
            criteria={
                "file_system": {
                    "sandboxed_to_restricted": CategoryTrustCriteria(
                        tasks_completed=5,
                        quality_score_min=0.0,
                    ),
                },
            },
        )
        strategy = PerCategoryTrustStrategy(config=config)

        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.SANDBOXED,
            category_levels={
                "file_system": ToolAccessLevel.SANDBOXED,
            },
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

        assert result.recommended_level == ToolAccessLevel.RESTRICTED

    async def test_criteria_not_met_stays_at_current(self) -> None:
        """Insufficient tasks keeps category at current level."""
        config = _make_per_category_config(
            initial_levels={
                "file_system": ToolAccessLevel.SANDBOXED,
            },
            criteria={
                "file_system": {
                    "sandboxed_to_restricted": CategoryTrustCriteria(
                        tasks_completed=50,
                        quality_score_min=9.0,
                    ),
                },
            },
        )
        strategy = PerCategoryTrustStrategy(config=config)

        state = TrustState(
            agent_id=NotBlankStr("agent-001"),
            global_level=ToolAccessLevel.SANDBOXED,
            category_levels={
                "file_system": ToolAccessLevel.SANDBOXED,
            },
        )
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=5.0,
            tasks_completed=3,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.recommended_level == ToolAccessLevel.SANDBOXED
        assert result.should_change is False
