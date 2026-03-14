"""Tests for CostOptimizer — downgrades, approval, routing, edge cases."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.budget._optimizer_helpers import _find_cheaper_model
from synthorg.budget.config import (
    AutoDowngradeConfig,
    BudgetAlertConfig,
    BudgetConfig,
)
from synthorg.budget.enums import BudgetAlertLevel
from synthorg.budget.optimizer import CostOptimizer
from synthorg.budget.optimizer_models import CostOptimizerConfig
from synthorg.budget.tracker import CostTracker
from synthorg.providers.routing.models import ResolvedModel
from tests.unit.budget.conftest import (
    OPT_END,
    OPT_START,
    make_cost_record,
    make_optimizer,
    make_resolver,
)

# ── Downgrade Recommendation Tests ────────────────────────────────


@pytest.mark.unit
class TestRecommendDowngrades:
    async def test_no_resolver_empty_result(self) -> None:
        optimizer, _ = make_optimizer()
        result = await optimizer.recommend_downgrades(start=OPT_START, end=OPT_END)
        assert result.recommendations == ()

    async def test_with_downgrade_path(self) -> None:
        resolver = make_resolver()
        bc = BudgetConfig(
            total_monthly=100.0,
            auto_downgrade=AutoDowngradeConfig(
                enabled=True,
                threshold=80,
                downgrade_map=(("large", "small"),),
            ),
        )
        tracker = CostTracker(budget_config=bc)
        optimizer = CostOptimizer(
            cost_tracker=tracker,
            budget_config=bc,
            model_resolver=resolver,
        )

        await tracker.record(
            make_cost_record(
                agent_id="alice",
                model="test-large-001",
                cost_usd=10.0,
                input_tokens=1000,
                output_tokens=0,
                timestamp=OPT_START + timedelta(hours=1),
            ),
        )
        await tracker.record(
            make_cost_record(
                agent_id="bob",
                model="test-small-001",
                cost_usd=0.1,
                input_tokens=1000,
                output_tokens=0,
                timestamp=OPT_START + timedelta(hours=1),
            ),
        )

        result = await optimizer.recommend_downgrades(start=OPT_START, end=OPT_END)
        assert len(result.recommendations) == 1
        rec = result.recommendations[0]
        assert rec.agent_id == "alice"
        assert rec.current_model == "test-large-001"
        assert rec.recommended_model == "test-small-001"
        assert rec.estimated_savings_per_1k > 0

    async def test_no_cheaper_model_empty(self) -> None:
        """No recommendation when agent already uses cheapest model."""
        resolver = make_resolver(
            [
                ResolvedModel(
                    provider_name="test-provider",
                    model_id="test-only-001",
                    alias="only",
                    cost_per_1k_input=0.01,
                    cost_per_1k_output=0.02,
                ),
            ]
        )
        bc = BudgetConfig(total_monthly=100.0)
        tracker = CostTracker(budget_config=bc)
        optimizer = CostOptimizer(
            cost_tracker=tracker,
            budget_config=bc,
            model_resolver=resolver,
        )

        await tracker.record(
            make_cost_record(
                agent_id="alice",
                model="test-only-001",
                cost_usd=10.0,
                input_tokens=1000,
                output_tokens=0,
                timestamp=OPT_START + timedelta(hours=1),
            ),
        )

        result = await optimizer.recommend_downgrades(start=OPT_START, end=OPT_END)
        assert result.recommendations == ()


# ── Evaluate Operation Tests ──────────────────────────────────────


@pytest.mark.unit
class TestEvaluateOperation:
    async def test_healthy_budget_approved(self) -> None:
        optimizer, tracker = make_optimizer()
        await tracker.record(
            make_cost_record(cost_usd=10.0, timestamp=OPT_START + timedelta(hours=1)),
        )
        decision = await optimizer.evaluate_operation(
            agent_id="alice",
            estimated_cost_usd=0.5,
            now=OPT_START + timedelta(days=15),
        )
        assert decision.approved is True
        assert decision.alert_level == BudgetAlertLevel.NORMAL

    async def test_hard_stop_denied(self) -> None:
        bc = BudgetConfig(
            total_monthly=100.0,
            alerts=BudgetAlertConfig(warn_at=75, critical_at=90, hard_stop_at=100),
        )
        optimizer, tracker = make_optimizer(budget_config=bc)

        await tracker.record(
            make_cost_record(cost_usd=100.0, timestamp=OPT_START + timedelta(hours=1)),
        )

        decision = await optimizer.evaluate_operation(
            agent_id="alice",
            estimated_cost_usd=1.0,
            now=OPT_START + timedelta(days=15),
        )
        assert decision.approved is False
        assert decision.alert_level == BudgetAlertLevel.HARD_STOP

    async def test_would_exceed_budget_denied(self) -> None:
        bc = BudgetConfig(
            total_monthly=100.0,
            alerts=BudgetAlertConfig(warn_at=75, critical_at=90, hard_stop_at=100),
        )
        optimizer, tracker = make_optimizer(budget_config=bc)

        # Spend 95% and request 10 more → projected 105% → HARD_STOP
        await tracker.record(
            make_cost_record(cost_usd=95.0, timestamp=OPT_START + timedelta(hours=1)),
        )

        decision = await optimizer.evaluate_operation(
            agent_id="alice",
            estimated_cost_usd=10.0,
            now=OPT_START + timedelta(days=15),
        )
        assert decision.approved is False
        # With projected alert level, this now triggers auto-deny
        assert "denied" in decision.reason.lower()

    async def test_warning_level_approved_with_conditions(self) -> None:
        bc = BudgetConfig(
            total_monthly=100.0,
            alerts=BudgetAlertConfig(warn_at=75, critical_at=90, hard_stop_at=100),
        )
        optimizer, tracker = make_optimizer(budget_config=bc)

        # Spend 80% (warning level)
        await tracker.record(
            make_cost_record(cost_usd=80.0, timestamp=OPT_START + timedelta(hours=1)),
        )

        decision = await optimizer.evaluate_operation(
            agent_id="alice",
            estimated_cost_usd=2.0,
            now=OPT_START + timedelta(days=15),
        )
        assert decision.approved is True
        assert decision.alert_level == BudgetAlertLevel.WARNING
        assert len(decision.conditions) > 0

    async def test_budget_enforcement_disabled(self) -> None:
        bc = BudgetConfig(total_monthly=0.0)
        optimizer, _ = make_optimizer(budget_config=bc)

        decision = await optimizer.evaluate_operation(
            agent_id="alice",
            estimated_cost_usd=100.0,
        )
        assert decision.approved is True
        assert "disabled" in decision.reason.lower()

    async def test_critical_level_auto_deny_with_custom_config(self) -> None:
        """Auto-deny at CRITICAL when configured."""
        bc = BudgetConfig(
            total_monthly=100.0,
            alerts=BudgetAlertConfig(warn_at=75, critical_at=90, hard_stop_at=100),
        )
        config = CostOptimizerConfig(
            approval_auto_deny_alert_level=BudgetAlertLevel.CRITICAL,
        )
        optimizer, tracker = make_optimizer(budget_config=bc, config=config)

        # Spend 92% (critical level)
        await tracker.record(
            make_cost_record(cost_usd=92.0, timestamp=OPT_START + timedelta(hours=1)),
        )

        decision = await optimizer.evaluate_operation(
            agent_id="alice",
            estimated_cost_usd=0.01,
            now=OPT_START + timedelta(days=15),
        )
        assert decision.approved is False
        assert decision.alert_level == BudgetAlertLevel.CRITICAL

    async def test_high_cost_condition(self) -> None:
        """High-cost warning condition when estimated cost >= threshold."""
        config = CostOptimizerConfig(approval_warn_threshold_usd=0.5)
        optimizer, _ = make_optimizer(config=config)

        decision = await optimizer.evaluate_operation(
            agent_id="alice",
            estimated_cost_usd=1.0,
            now=OPT_START + timedelta(days=15),
        )
        assert decision.approved is True
        assert any("High-cost" in c for c in decision.conditions)

    async def test_negative_estimated_cost_rejected(self) -> None:
        """Negative estimated_cost_usd raises ValueError."""
        optimizer, _ = make_optimizer()
        with pytest.raises(ValueError, match="estimated_cost_usd must be >= 0"):
            await optimizer.evaluate_operation(
                agent_id="alice",
                estimated_cost_usd=-1.0,
            )

    async def test_projected_alert_level_used_for_auto_deny(self) -> None:
        """Auto-deny uses projected alert level, not current."""
        bc = BudgetConfig(
            total_monthly=100.0,
            alerts=BudgetAlertConfig(warn_at=75, critical_at=90, hard_stop_at=100),
        )
        config = CostOptimizerConfig(
            approval_auto_deny_alert_level=BudgetAlertLevel.HARD_STOP,
        )
        optimizer, tracker = make_optimizer(budget_config=bc, config=config)

        # Spend 95% — current alert is CRITICAL, but requesting 10
        # would push to 105% → projected HARD_STOP → denied
        await tracker.record(
            make_cost_record(cost_usd=95.0, timestamp=OPT_START + timedelta(hours=1)),
        )

        decision = await optimizer.evaluate_operation(
            agent_id="alice",
            estimated_cost_usd=10.0,
            now=OPT_START + timedelta(days=15),
        )
        assert decision.approved is False
        assert "projected" in decision.reason.lower()


# ── Routing Optimization Tests ──────────────────────────────────


@pytest.mark.unit
class TestSuggestRoutingOptimizations:
    async def test_no_resolver_empty_result(self) -> None:
        optimizer, _ = make_optimizer()
        result = await optimizer.suggest_routing_optimizations(
            start=OPT_START,
            end=OPT_END,
        )
        assert result.suggestions == ()
        assert result.agents_analyzed == 0

    async def test_no_records_empty_suggestions(self) -> None:
        resolver = make_resolver()
        optimizer, _ = make_optimizer(model_resolver=resolver)
        result = await optimizer.suggest_routing_optimizations(
            start=OPT_START,
            end=OPT_END,
        )
        assert result.suggestions == ()
        assert result.agents_analyzed == 0

    async def test_suggests_cheaper_model(self) -> None:
        resolver = make_resolver()
        optimizer, tracker = make_optimizer(model_resolver=resolver)

        await tracker.record(
            make_cost_record(
                agent_id="alice",
                model="test-large-001",
                cost_usd=5.0,
                input_tokens=1000,
                output_tokens=500,
                timestamp=OPT_START + timedelta(hours=1),
            ),
        )

        result = await optimizer.suggest_routing_optimizations(
            start=OPT_START,
            end=OPT_END,
        )
        assert len(result.suggestions) == 1
        suggestion = result.suggestions[0]
        assert suggestion.agent_id == "alice"
        assert suggestion.current_model == "test-large-001"
        assert suggestion.estimated_savings_per_1k > 0
        assert result.total_estimated_savings_per_1k > 0

    async def test_no_suggestion_for_cheapest_model(self) -> None:
        resolver = make_resolver()
        optimizer, tracker = make_optimizer(model_resolver=resolver)

        await tracker.record(
            make_cost_record(
                agent_id="alice",
                model="test-small-001",
                cost_usd=0.1,
                input_tokens=1000,
                output_tokens=500,
                timestamp=OPT_START + timedelta(hours=1),
            ),
        )

        result = await optimizer.suggest_routing_optimizations(
            start=OPT_START,
            end=OPT_END,
        )
        assert result.suggestions == ()
        assert result.agents_analyzed == 1

    async def test_start_after_end_rejected(self) -> None:
        optimizer, _ = make_optimizer()
        with pytest.raises(ValueError, match=r"start .* must be before end"):
            await optimizer.suggest_routing_optimizations(start=OPT_END, end=OPT_START)

    async def test_context_window_respected(self) -> None:
        """Suggestions only include models with sufficient context window."""
        models = [
            ResolvedModel(
                provider_name="test-provider",
                model_id="test-large-001",
                alias="large",
                cost_per_1k_input=0.03,
                cost_per_1k_output=0.06,
                max_context=200000,
            ),
            ResolvedModel(
                provider_name="test-provider",
                model_id="test-small-001",
                alias="small",
                cost_per_1k_input=0.001,
                cost_per_1k_output=0.002,
                max_context=50000,  # Smaller context than large
            ),
        ]
        resolver = make_resolver(models)
        optimizer, tracker = make_optimizer(model_resolver=resolver)

        await tracker.record(
            make_cost_record(
                agent_id="alice",
                model="test-large-001",
                cost_usd=5.0,
                timestamp=OPT_START + timedelta(hours=1),
            ),
        )

        result = await optimizer.suggest_routing_optimizations(
            start=OPT_START,
            end=OPT_END,
        )
        # small has insufficient context window → no suggestion
        assert result.suggestions == ()


# ── Edge Case Tests ──────────────────────────────────────────────


@pytest.mark.unit
class TestEdgeCases:
    async def test_find_cheaper_model_exercises_fallback_path(self) -> None:
        """_find_cheaper_model selects the cheapest with sufficient context."""
        resolver = make_resolver()
        # Directly call _find_cheaper_model to verify it picks the cheapest
        result = _find_cheaper_model(0.09, resolver)
        assert result is not None
        assert result.model_id == "test-small-001"

    async def test_find_cheaper_model_respects_min_context(self) -> None:
        """_find_cheaper_model skips models with insufficient context."""
        models = [
            ResolvedModel(
                provider_name="test-provider",
                model_id="test-large-001",
                alias="large",
                cost_per_1k_input=0.03,
                cost_per_1k_output=0.06,
                max_context=200000,
            ),
            ResolvedModel(
                provider_name="test-provider",
                model_id="test-small-001",
                alias="small",
                cost_per_1k_input=0.001,
                cost_per_1k_output=0.002,
                max_context=50000,
            ),
        ]
        resolver = make_resolver(models)
        # Require 200k context — small model has only 50k
        result = _find_cheaper_model(0.09, resolver, min_context=200000)
        assert result is None

    async def test_budget_pressure_percent_reflects_spending(self) -> None:
        """budget_pressure_percent reflects actual spend vs budget."""
        from synthorg.budget.billing import billing_period_start

        resolver = make_resolver()
        bc = BudgetConfig(total_monthly=100.0)
        tracker = CostTracker(budget_config=bc)
        optimizer = CostOptimizer(
            cost_tracker=tracker,
            budget_config=bc,
            model_resolver=resolver,
        )
        now = datetime.now(UTC)
        period_start = billing_period_start(bc.reset_day, now=now)
        await tracker.record(
            make_cost_record(
                cost_usd=60.0,
                timestamp=period_start + timedelta(hours=1),
            ),
        )
        analysis_start = period_start
        analysis_end = now + timedelta(days=1)
        result = await optimizer.recommend_downgrades(
            start=analysis_start, end=analysis_end
        )
        assert result.budget_pressure_percent == 60.0

    async def test_downgrade_target_not_resolved(self) -> None:
        """No recommendation when downgrade target doesn't resolve."""
        resolver = make_resolver(
            [
                ResolvedModel(
                    provider_name="test-provider",
                    model_id="test-large-001",
                    alias="large",
                    cost_per_1k_input=0.03,
                    cost_per_1k_output=0.06,
                ),
            ]
        )
        bc = BudgetConfig(
            total_monthly=100.0,
            auto_downgrade=AutoDowngradeConfig(
                enabled=True,
                threshold=80,
                downgrade_map=(("large", "nonexistent"),),
            ),
        )
        tracker = CostTracker(budget_config=bc)
        optimizer = CostOptimizer(
            cost_tracker=tracker,
            budget_config=bc,
            model_resolver=resolver,
        )

        await tracker.record(
            make_cost_record(
                agent_id="alice",
                model="test-large-001",
                cost_usd=10.0,
                input_tokens=1000,
                output_tokens=0,
                timestamp=OPT_START + timedelta(hours=1),
            ),
        )
        await tracker.record(
            make_cost_record(
                agent_id="bob",
                model="test-large-001",
                cost_usd=0.1,
                input_tokens=1000,
                output_tokens=0,
                timestamp=OPT_START + timedelta(hours=1),
            ),
        )

        result = await optimizer.recommend_downgrades(start=OPT_START, end=OPT_END)
        # Target "nonexistent" can't be resolved → no recommendation
        assert result.recommendations == ()

    async def test_no_resolver_returns_real_budget_pressure(self) -> None:
        """recommend_downgrades without resolver still reports real pressure."""
        from synthorg.budget.billing import billing_period_start

        bc = BudgetConfig(total_monthly=100.0)
        tracker = CostTracker(budget_config=bc)
        optimizer = CostOptimizer(
            cost_tracker=tracker,
            budget_config=bc,
            model_resolver=None,
        )
        now = datetime.now(UTC)
        period_start = billing_period_start(bc.reset_day, now=now)
        await tracker.record(
            make_cost_record(
                cost_usd=40.0,
                timestamp=period_start + timedelta(hours=1),
            ),
        )
        analysis_start = period_start
        analysis_end = now + timedelta(days=1)
        result = await optimizer.recommend_downgrades(
            start=analysis_start, end=analysis_end
        )
        assert result.recommendations == ()
        assert result.budget_pressure_percent == 40.0
