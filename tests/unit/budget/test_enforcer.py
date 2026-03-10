"""Tests for BudgetEnforcer service."""

import contextlib
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from unittest.mock import patch
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Iterator

import pytest

from ai_company.budget.config import (
    AutoDowngradeConfig,
    BudgetAlertConfig,
    BudgetConfig,
)
from ai_company.budget.enforcer import BudgetEnforcer
from ai_company.budget.errors import BudgetExhaustedError, DailyLimitExceededError
from ai_company.budget.tracker import CostTracker
from ai_company.core.agent import AgentIdentity, ModelConfig
from ai_company.core.enums import TaskStatus, TaskType
from ai_company.core.task import Task
from ai_company.engine.context import AgentContext
from ai_company.observability.events.budget import BUDGET_ALERT_THRESHOLD_CROSSED
from ai_company.providers.models import TokenUsage
from ai_company.providers.routing.models import ResolvedModel
from ai_company.providers.routing.resolver import ModelResolver

from .conftest import make_cost_record

pytestmark = pytest.mark.timeout(30)

# Timestamps within the test billing period (March 2026)
_BILLING_START = datetime(2026, 3, 1, tzinfo=UTC)
_DAY_START = datetime(2026, 3, 15, tzinfo=UTC)
_RECORD_TS = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_budget_config(  # noqa: PLR0913
    *,
    total_monthly: float = 100.0,
    warn_at: int = 75,
    critical_at: int = 90,
    hard_stop_at: int = 100,
    per_agent_daily_limit: float = 10.0,
    per_task_limit: float = 5.0,
    reset_day: int = 1,
    auto_downgrade: AutoDowngradeConfig | None = None,
) -> BudgetConfig:
    return BudgetConfig(
        total_monthly=total_monthly,
        alerts=BudgetAlertConfig(
            warn_at=warn_at,
            critical_at=critical_at,
            hard_stop_at=hard_stop_at,
        ),
        per_agent_daily_limit=per_agent_daily_limit,
        per_task_limit=per_task_limit,
        reset_day=reset_day,
        auto_downgrade=auto_downgrade or AutoDowngradeConfig(),
    )


def _make_identity(
    *,
    model_id: str = "test-large-001",
    provider: str = "test-provider",
) -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider=provider, model_id=model_id),
        hiring_date=date(2026, 1, 1),
    )


def _make_task(
    *,
    agent_id: str,
    budget_limit: float = 0.0,
) -> Task:
    return Task(
        id="task-001",
        title="Test task",
        description="A test task",
        type=TaskType.DEVELOPMENT,
        project="proj-001",
        created_by="manager",
        assigned_to=agent_id,
        status=TaskStatus.ASSIGNED,
        budget_limit=budget_limit,
    )


def _make_resolver(
    models: dict[str, ResolvedModel] | None = None,
) -> ModelResolver:
    index = models or {}
    return ModelResolver(index)


def _resolved(
    *,
    model_id: str,
    provider: str = "test-provider",
    alias: str | None = None,
) -> ResolvedModel:
    return ResolvedModel(
        provider_name=provider,
        model_id=model_id,
        alias=alias,
    )


def _ctx_with_cost(
    identity: AgentIdentity,
    task: Task,
    cost_usd: float,
) -> AgentContext:
    """Build an AgentContext with a specific accumulated cost."""
    ctx = AgentContext.from_identity(identity, task=task)
    return ctx.model_copy(
        update={
            "accumulated_cost": TokenUsage(
                input_tokens=100,
                output_tokens=50,
                cost_usd=cost_usd,
            ),
        },
    )


@contextlib.contextmanager
def _patch_periods() -> Iterator[None]:
    """Patch billing and daily period starts to fixed test timestamps."""
    with (
        patch(
            "ai_company.budget.enforcer.billing_period_start",
            return_value=_BILLING_START,
        ),
        patch(
            "ai_company.budget.enforcer.daily_period_start",
            return_value=_DAY_START,
        ),
    ):
        yield


# ── Pre-flight checks ───────────────────────────────────────────────


@pytest.mark.unit
class TestCheckCanExecute:
    """Tests for BudgetEnforcer.check_can_execute()."""

    async def test_passes_when_under_budget(self) -> None:
        """Monthly budget not exceeded passes without exception."""
        cfg = _make_budget_config(
            total_monthly=100.0,
            per_agent_daily_limit=50.0,
        )
        tracker = CostTracker(budget_config=cfg)
        await tracker.record(
            make_cost_record(
                cost_usd=30.0,
                input_tokens=100,
                output_tokens=50,
                timestamp=_RECORD_TS,
            ),
        )
        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
        )
        with _patch_periods():
            await enforcer.check_can_execute("alice")

    async def test_raises_at_exactly_hard_stop(self) -> None:
        """Monthly budget at exactly 100% raises BudgetExhaustedError."""
        cfg = _make_budget_config(total_monthly=100.0, hard_stop_at=100)
        tracker = CostTracker(budget_config=cfg)
        await tracker.record(
            make_cost_record(
                cost_usd=100.0,
                input_tokens=100,
                output_tokens=50,
                timestamp=_RECORD_TS,
            ),
        )
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)

        with (
            _patch_periods(),
            pytest.raises(
                BudgetExhaustedError,
                match="Monthly budget exhausted",
            ),
        ):
            await enforcer.check_can_execute("alice")

    async def test_raises_over_hard_stop(self) -> None:
        """Monthly budget over 100% raises BudgetExhaustedError."""
        cfg = _make_budget_config(total_monthly=100.0, hard_stop_at=100)
        tracker = CostTracker(budget_config=cfg)
        await tracker.record(
            make_cost_record(
                cost_usd=110.0,
                input_tokens=100,
                output_tokens=50,
                timestamp=_RECORD_TS,
            ),
        )
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)

        with _patch_periods(), pytest.raises(BudgetExhaustedError):
            await enforcer.check_can_execute("alice")

    async def test_daily_limit_at_exact_limit_raises(self) -> None:
        """Daily limit at exactly the limit raises DailyLimitExceededError."""
        cfg = _make_budget_config(
            total_monthly=100.0,
            per_agent_daily_limit=10.0,
        )
        tracker = CostTracker(budget_config=cfg)
        await tracker.record(
            make_cost_record(
                agent_id="alice",
                cost_usd=10.0,
                input_tokens=100,
                output_tokens=50,
                timestamp=_RECORD_TS,
            ),
        )
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)

        with (
            _patch_periods(),
            pytest.raises(
                DailyLimitExceededError,
                match="daily limit exceeded",
            ),
        ):
            await enforcer.check_can_execute("alice")

    async def test_daily_limit_not_exceeded_passes(self) -> None:
        """Daily limit not reached passes without exception."""
        cfg = _make_budget_config(per_agent_daily_limit=10.0)
        tracker = CostTracker(budget_config=cfg)
        await tracker.record(
            make_cost_record(
                agent_id="alice",
                cost_usd=5.0,
                input_tokens=100,
                output_tokens=50,
                timestamp=_RECORD_TS,
            ),
        )
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)

        with _patch_periods():
            await enforcer.check_can_execute("alice")

    async def test_monthly_disabled_passes_without_daily_spend(self) -> None:
        """Monthly disabled (total_monthly=0) passes when no daily spend."""
        cfg = _make_budget_config(total_monthly=0.0)
        tracker = CostTracker(budget_config=cfg)
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)
        await enforcer.check_can_execute("alice")

    async def test_daily_limit_enforced_when_monthly_disabled(self) -> None:
        """Daily limit still fires when total_monthly=0."""
        cfg = _make_budget_config(
            total_monthly=0.0,
            per_agent_daily_limit=10.0,
        )
        tracker = CostTracker(budget_config=cfg)
        await tracker.record(
            make_cost_record(
                agent_id="alice",
                cost_usd=10.0,
                input_tokens=100,
                output_tokens=50,
                timestamp=_RECORD_TS,
            ),
        )
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)

        with (
            _patch_periods(),
            pytest.raises(
                DailyLimitExceededError,
                match="daily limit exceeded",
            ),
        ):
            await enforcer.check_can_execute("alice")

    async def test_daily_limit_disabled_skips_check(self) -> None:
        """Daily limit of 0 skips the daily check entirely."""
        cfg = _make_budget_config(per_agent_daily_limit=0.0)
        tracker = CostTracker(budget_config=cfg)
        await tracker.record(
            make_cost_record(
                agent_id="alice",
                cost_usd=50.0,
                input_tokens=100,
                output_tokens=50,
                timestamp=_RECORD_TS,
            ),
        )
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)

        with _patch_periods():
            await enforcer.check_can_execute("alice")


# ── Auto-downgrade ───────────────────────────────────────────────────


@pytest.mark.unit
class TestResolveModel:
    """Tests for BudgetEnforcer.resolve_model()."""

    async def test_below_threshold_returns_unchanged(self) -> None:
        """Budget below downgrade threshold returns identity unchanged."""
        cfg = _make_budget_config(
            auto_downgrade=AutoDowngradeConfig(
                enabled=True,
                threshold=85,
                downgrade_map=(("large", "medium"),),
            ),
        )
        tracker = CostTracker(budget_config=cfg)
        # 50% usage — below 85% threshold
        await tracker.record(
            make_cost_record(
                cost_usd=50.0,
                input_tokens=100,
                output_tokens=50,
                timestamp=_RECORD_TS,
            ),
        )
        resolver = _make_resolver(
            {"test-large-001": _resolved(model_id="test-large-001", alias="large")},
        )
        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            model_resolver=resolver,
        )
        identity = _make_identity()

        with _patch_periods():
            result = await enforcer.resolve_model(identity)

        assert result.model.model_id == "test-large-001"

    async def test_above_threshold_with_mapping_downgrades(self) -> None:
        """Budget above threshold with matching alias downgrades the model."""
        cfg = _make_budget_config(
            auto_downgrade=AutoDowngradeConfig(
                enabled=True,
                threshold=85,
                downgrade_map=(("large", "medium"),),
            ),
        )
        tracker = CostTracker(budget_config=cfg)
        # 90% usage — above 85% threshold
        await tracker.record(
            make_cost_record(
                cost_usd=90.0,
                input_tokens=100,
                output_tokens=50,
                timestamp=_RECORD_TS,
            ),
        )
        resolver = _make_resolver(
            {
                "test-large-001": _resolved(
                    model_id="test-large-001",
                    alias="large",
                ),
                "large": _resolved(model_id="test-large-001", alias="large"),
                "medium": _resolved(
                    model_id="test-medium-001",
                    provider="test-provider",
                    alias="medium",
                ),
            }
        )
        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            model_resolver=resolver,
        )
        identity = _make_identity(model_id="test-large-001")

        with _patch_periods():
            result = await enforcer.resolve_model(identity)

        assert result.model.model_id == "test-medium-001"
        assert result.model.provider == "test-provider"

    async def test_above_threshold_no_matching_alias_unchanged(self) -> None:
        """Budget above threshold but no matching alias returns unchanged."""
        cfg = _make_budget_config(
            auto_downgrade=AutoDowngradeConfig(
                enabled=True,
                threshold=85,
                downgrade_map=(("small", "tiny"),),
            ),
        )
        tracker = CostTracker(budget_config=cfg)
        await tracker.record(
            make_cost_record(
                cost_usd=90.0,
                input_tokens=100,
                output_tokens=50,
                timestamp=_RECORD_TS,
            ),
        )
        resolver = _make_resolver(
            {
                "test-large-001": _resolved(
                    model_id="test-large-001",
                    alias="large",
                ),
            }
        )
        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            model_resolver=resolver,
        )
        identity = _make_identity()

        with _patch_periods():
            result = await enforcer.resolve_model(identity)

        assert result.model.model_id == "test-large-001"

    async def test_no_model_resolver_returns_unchanged(self) -> None:
        """No model_resolver provided returns identity unchanged."""
        cfg = _make_budget_config(
            auto_downgrade=AutoDowngradeConfig(
                enabled=True,
                threshold=85,
                downgrade_map=(("large", "medium"),),
            ),
        )
        tracker = CostTracker(budget_config=cfg)
        await tracker.record(
            make_cost_record(
                cost_usd=90.0,
                input_tokens=100,
                output_tokens=50,
                timestamp=_RECORD_TS,
            ),
        )
        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            model_resolver=None,
        )
        identity = _make_identity()

        result = await enforcer.resolve_model(identity)
        assert result.model.model_id == "test-large-001"

    async def test_disabled_returns_unchanged(self) -> None:
        """Auto-downgrade disabled returns identity unchanged."""
        cfg = _make_budget_config(
            auto_downgrade=AutoDowngradeConfig(enabled=False),
        )
        tracker = CostTracker(budget_config=cfg)
        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
        )
        identity = _make_identity()

        result = await enforcer.resolve_model(identity)
        assert result.model.model_id == "test-large-001"

    async def test_at_exact_threshold_applies_downgrade(self) -> None:
        """Budget at exactly the threshold applies downgrade."""
        cfg = _make_budget_config(
            auto_downgrade=AutoDowngradeConfig(
                enabled=True,
                threshold=85,
                downgrade_map=(("large", "medium"),),
            ),
        )
        tracker = CostTracker(budget_config=cfg)
        # Exactly 85% usage
        await tracker.record(
            make_cost_record(
                cost_usd=85.0,
                input_tokens=100,
                output_tokens=50,
                timestamp=_RECORD_TS,
            ),
        )
        resolver = _make_resolver(
            {
                "test-large-001": _resolved(
                    model_id="test-large-001",
                    alias="large",
                ),
                "large": _resolved(
                    model_id="test-large-001",
                    alias="large",
                ),
                "medium": _resolved(
                    model_id="test-medium-001",
                    alias="medium",
                ),
            }
        )
        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            model_resolver=resolver,
        )
        identity = _make_identity(model_id="test-large-001")

        with _patch_periods():
            result = await enforcer.resolve_model(identity)

        # At exactly threshold → downgrade applies (< is strict)
        assert result.model.model_id == "test-medium-001"

    async def test_resolved_model_has_no_alias_unchanged(self) -> None:
        """Model in resolver but with no alias returns unchanged."""
        cfg = _make_budget_config(
            auto_downgrade=AutoDowngradeConfig(
                enabled=True,
                threshold=85,
                downgrade_map=(("large", "medium"),),
            ),
        )
        tracker = CostTracker(budget_config=cfg)
        await tracker.record(
            make_cost_record(
                cost_usd=90.0,
                input_tokens=100,
                output_tokens=50,
                timestamp=_RECORD_TS,
            ),
        )
        # Model registered without an alias
        resolver = _make_resolver(
            {
                "test-large-001": _resolved(
                    model_id="test-large-001",
                    alias=None,
                ),
            }
        )
        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            model_resolver=resolver,
        )
        identity = _make_identity()

        with _patch_periods():
            result = await enforcer.resolve_model(identity)

        assert result.model.model_id == "test-large-001"

    async def test_target_alias_not_resolvable_unchanged(self) -> None:
        """Target alias in downgrade map but not in resolver skips."""
        cfg = _make_budget_config(
            auto_downgrade=AutoDowngradeConfig(
                enabled=True,
                threshold=85,
                downgrade_map=(("large", "nonexistent"),),
            ),
        )
        tracker = CostTracker(budget_config=cfg)
        await tracker.record(
            make_cost_record(
                cost_usd=90.0,
                input_tokens=100,
                output_tokens=50,
                timestamp=_RECORD_TS,
            ),
        )
        resolver = _make_resolver(
            {
                "test-large-001": _resolved(
                    model_id="test-large-001",
                    alias="large",
                ),
                "large": _resolved(
                    model_id="test-large-001",
                    alias="large",
                ),
                # "nonexistent" is NOT registered
            }
        )
        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            model_resolver=resolver,
        )
        identity = _make_identity()

        with _patch_periods():
            result = await enforcer.resolve_model(identity)

        assert result.model.model_id == "test-large-001"

    async def test_chain_downgrade_applies_first_match_only(self) -> None:
        """Only the first matching downgrade_map entry applies."""
        cfg = _make_budget_config(
            auto_downgrade=AutoDowngradeConfig(
                enabled=True,
                threshold=85,
                downgrade_map=(
                    ("large", "medium"),
                    ("medium", "small"),
                ),
            ),
        )
        tracker = CostTracker(budget_config=cfg)
        await tracker.record(
            make_cost_record(
                cost_usd=90.0,
                input_tokens=100,
                output_tokens=50,
                timestamp=_RECORD_TS,
            ),
        )
        resolver = _make_resolver(
            {
                "test-large-001": _resolved(
                    model_id="test-large-001",
                    alias="large",
                ),
                "large": _resolved(model_id="test-large-001", alias="large"),
                "medium": _resolved(
                    model_id="test-medium-001",
                    alias="medium",
                ),
                "small": _resolved(model_id="test-small-001", alias="small"),
            }
        )
        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            model_resolver=resolver,
        )
        identity = _make_identity(model_id="test-large-001")

        with _patch_periods():
            result = await enforcer.resolve_model(identity)

        # Should downgrade to medium, NOT to small
        assert result.model.model_id == "test-medium-001"


# ── Budget checker factory ───────────────────────────────────────────


@pytest.mark.unit
class TestMakeBudgetChecker:
    """Tests for BudgetEnforcer.make_budget_checker()."""

    async def test_returns_none_when_all_disabled(self) -> None:
        """Returns None when all limits are disabled."""
        cfg = _make_budget_config(
            total_monthly=0.0,
            per_agent_daily_limit=0.0,
        )
        tracker = CostTracker(budget_config=cfg)
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)
        identity = _make_identity()
        task = _make_task(
            agent_id=str(identity.id),
            budget_limit=0.0,
        )

        checker = await enforcer.make_budget_checker(task, str(identity.id))
        assert checker is None

    async def test_returns_checker_when_only_task_limit_active(self) -> None:
        """Returns a checker (not None) when only task_limit is set."""
        cfg = _make_budget_config(
            total_monthly=0.0,
            per_agent_daily_limit=0.0,
        )
        tracker = CostTracker(budget_config=cfg)
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)
        identity = _make_identity()
        task = _make_task(
            agent_id=str(identity.id),
            budget_limit=5.0,
        )

        checker = await enforcer.make_budget_checker(task, str(identity.id))
        assert checker is not None

        # Under limit → not exhausted
        ctx_under = _ctx_with_cost(identity, task, 4.99)
        assert checker(ctx_under) is False

        # At limit → exhausted
        ctx_at = _ctx_with_cost(identity, task, 5.0)
        assert checker(ctx_at) is True

    async def test_task_budget_exhaustion(self) -> None:
        """Checker detects task budget exhaustion at exact limit."""
        cfg = _make_budget_config(total_monthly=100.0)
        tracker = CostTracker(budget_config=cfg)
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)
        identity = _make_identity()
        task = _make_task(
            agent_id=str(identity.id),
            budget_limit=5.0,
        )

        with _patch_periods():
            checker = await enforcer.make_budget_checker(task, str(identity.id))

        assert checker is not None

        # Under limit
        ctx_under = _ctx_with_cost(identity, task, 4.99)
        assert checker(ctx_under) is False

        # At limit
        ctx_at = _ctx_with_cost(identity, task, 5.0)
        assert checker(ctx_at) is True

    async def test_monthly_hard_stop(self) -> None:
        """Checker detects monthly hard stop (baseline + running cost)."""
        cfg = _make_budget_config(
            total_monthly=100.0,
            hard_stop_at=100,
            per_agent_daily_limit=0.0,
        )
        tracker = CostTracker(budget_config=cfg)
        # Pre-existing monthly spend of 90
        await tracker.record(
            make_cost_record(
                cost_usd=90.0,
                input_tokens=100,
                output_tokens=50,
                timestamp=_RECORD_TS,
            ),
        )
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)
        identity = _make_identity()
        task = _make_task(agent_id=str(identity.id))

        with _patch_periods():
            checker = await enforcer.make_budget_checker(task, str(identity.id))

        assert checker is not None

        # Running cost of 9 → total 99 → under 100
        ctx_under = _ctx_with_cost(identity, task, 9.0)
        assert checker(ctx_under) is False

        # Running cost of 10 → total 100 → at hard stop
        ctx_at = _ctx_with_cost(identity, task, 10.0)
        assert checker(ctx_at) is True

    async def test_daily_limit_in_checker(self) -> None:
        """Checker detects daily limit (baseline + running cost)."""
        cfg = _make_budget_config(
            total_monthly=100.0,
            per_agent_daily_limit=10.0,
        )
        tracker = CostTracker(budget_config=cfg)
        # Pre-existing daily spend of 8
        await tracker.record(
            make_cost_record(
                agent_id="alice",
                cost_usd=8.0,
                input_tokens=100,
                output_tokens=50,
                timestamp=_RECORD_TS,
            ),
        )
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)
        identity = _make_identity()
        task = _make_task(agent_id=str(identity.id))

        with _patch_periods():
            checker = await enforcer.make_budget_checker(task, "alice")

        assert checker is not None

        # Running cost of 1 → daily total 9 → under 10
        ctx_under = _ctx_with_cost(identity, task, 1.0)
        assert checker(ctx_under) is False

        # Running cost of 2 → daily total 10 → at limit
        ctx_at = _ctx_with_cost(identity, task, 2.0)
        assert checker(ctx_at) is True

    @pytest.mark.parametrize(
        ("baseline", "running", "expected_exhausted"),
        [
            (74.0, 0.9, False),  # 74.9% → NORMAL, not exhausted
            (74.0, 1.0, False),  # 75.0% → WARNING alert, not exhausted
            (89.0, 1.0, False),  # 90.0% → CRITICAL alert, not exhausted
            (99.0, 1.0, True),  # 100.0% → HARD_STOP, exhausted
        ],
        ids=["74.9_normal", "75.0_warning", "90.0_critical", "100.0_hard_stop"],
    )
    async def test_alert_thresholds(
        self,
        baseline: float,
        running: float,
        expected_exhausted: bool,
    ) -> None:
        """Alert fires at exact threshold percentages."""
        cfg = _make_budget_config(
            total_monthly=100.0,
            warn_at=75,
            critical_at=90,
            hard_stop_at=100,
            per_agent_daily_limit=0.0,
        )
        tracker = CostTracker(budget_config=cfg)
        await tracker.record(
            make_cost_record(
                cost_usd=baseline,
                input_tokens=100,
                output_tokens=50,
                timestamp=_RECORD_TS,
            ),
        )
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)
        identity = _make_identity()
        task = _make_task(agent_id=str(identity.id))

        with _patch_periods():
            checker = await enforcer.make_budget_checker(task, str(identity.id))

        assert checker is not None
        ctx = _ctx_with_cost(identity, task, running)
        assert checker(ctx) is expected_exhausted

    async def test_alert_deduplication(self) -> None:
        """Same alert level is not logged twice."""
        cfg = _make_budget_config(
            total_monthly=100.0,
            warn_at=75,
            critical_at=90,
            hard_stop_at=100,
            per_agent_daily_limit=0.0,
        )
        tracker = CostTracker(budget_config=cfg)
        # Baseline of 70
        await tracker.record(
            make_cost_record(
                cost_usd=70.0,
                input_tokens=100,
                output_tokens=50,
                timestamp=_RECORD_TS,
            ),
        )
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)
        identity = _make_identity()
        task = _make_task(agent_id=str(identity.id))

        with _patch_periods():
            checker = await enforcer.make_budget_checker(task, str(identity.id))

        assert checker is not None

        # Context at 75% (baseline 70 + running 5)
        ctx_warning = _ctx_with_cost(identity, task, 5.0)

        # First call at 75% → should emit WARNING
        with patch(
            "ai_company.budget.enforcer.logger",
        ) as mock_logger:
            checker(ctx_warning)
            warn_calls = [
                c
                for c in mock_logger.warning.call_args_list
                if c[0][0] == BUDGET_ALERT_THRESHOLD_CROSSED
            ]
            assert len(warn_calls) == 1

        # Second call at same level → should NOT emit again
        with patch(
            "ai_company.budget.enforcer.logger",
        ) as mock_logger2:
            checker(ctx_warning)
            warn_calls2 = [
                c
                for c in mock_logger2.warning.call_args_list
                if c[0][0] == BUDGET_ALERT_THRESHOLD_CROSSED
            ]
            assert len(warn_calls2) == 0


# ── Graceful degradation ─────────────────────────────────────────────


@pytest.mark.unit
class TestGracefulDegradation:
    """Tests for CostTracker failure fallback paths."""

    async def test_resolve_model_returns_unchanged_on_tracker_error(
        self,
    ) -> None:
        """CostTracker failure in resolve_model returns identity unchanged."""
        cfg = _make_budget_config(
            auto_downgrade=AutoDowngradeConfig(
                enabled=True,
                threshold=85,
                downgrade_map=(("large", "medium"),),
            ),
        )
        tracker = CostTracker(budget_config=cfg)
        resolver = _make_resolver(
            {
                "test-large-001": _resolved(
                    model_id="test-large-001",
                    alias="large",
                ),
            }
        )
        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            model_resolver=resolver,
        )
        identity = _make_identity()

        with patch.object(
            tracker,
            "get_total_cost",
            side_effect=RuntimeError("db connection failed"),
        ):
            result = await enforcer.resolve_model(identity)

        assert result.model.model_id == "test-large-001"

    async def test_resolve_model_propagates_memory_error(self) -> None:
        """MemoryError from CostTracker in resolve_model is re-raised."""
        cfg = _make_budget_config(
            auto_downgrade=AutoDowngradeConfig(
                enabled=True,
                threshold=85,
                downgrade_map=(("large", "medium"),),
            ),
        )
        tracker = CostTracker(budget_config=cfg)
        resolver = _make_resolver(
            {
                "test-large-001": _resolved(
                    model_id="test-large-001",
                    alias="large",
                ),
            }
        )
        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            model_resolver=resolver,
        )
        identity = _make_identity()

        with (
            patch.object(
                tracker,
                "get_total_cost",
                side_effect=MemoryError("OOM"),
            ),
            pytest.raises(MemoryError, match="OOM"),
        ):
            await enforcer.resolve_model(identity)

    async def test_make_budget_checker_falls_back_on_tracker_error(
        self,
    ) -> None:
        """CostTracker failure in make_budget_checker still returns a checker."""
        cfg = _make_budget_config(
            total_monthly=100.0,
            per_agent_daily_limit=10.0,
        )
        tracker = CostTracker(budget_config=cfg)
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)
        identity = _make_identity()
        task = _make_task(
            agent_id=str(identity.id),
            budget_limit=5.0,
        )

        with patch.object(
            tracker,
            "get_total_cost",
            side_effect=RuntimeError("db connection failed"),
        ):
            checker = await enforcer.make_budget_checker(
                task,
                str(identity.id),
            )

        # Checker should still be returned (not None)
        assert checker is not None

        # Task limit should still be enforced
        ctx_at = _ctx_with_cost(identity, task, 5.0)
        assert checker(ctx_at) is True

    async def test_make_budget_checker_propagates_memory_error(
        self,
    ) -> None:
        """MemoryError from CostTracker in make_budget_checker is re-raised."""
        cfg = _make_budget_config(total_monthly=100.0)
        tracker = CostTracker(budget_config=cfg)
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)
        identity = _make_identity()
        task = _make_task(agent_id=str(identity.id))

        with (
            patch.object(
                tracker,
                "get_total_cost",
                side_effect=MemoryError("OOM"),
            ),
            pytest.raises(MemoryError, match="OOM"),
        ):
            await enforcer.make_budget_checker(task, str(identity.id))

    async def test_checker_task_limit_zero_does_not_trigger(self) -> None:
        """Checker with task_limit=0 but monthly active ignores task limit."""
        cfg = _make_budget_config(
            total_monthly=100.0,
            per_agent_daily_limit=0.0,
        )
        tracker = CostTracker(budget_config=cfg)
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)
        identity = _make_identity()
        task = _make_task(
            agent_id=str(identity.id),
            budget_limit=0.0,
        )

        with _patch_periods():
            checker = await enforcer.make_budget_checker(
                task,
                str(identity.id),
            )

        assert checker is not None

        # High running cost should not trigger task limit (disabled)
        # but should not hit monthly hard stop either (no baseline spend)
        ctx = _ctx_with_cost(identity, task, 50.0)
        assert checker(ctx) is False


# ── cost_tracker property ────────────────────────────────────────────


@pytest.mark.unit
class TestCostTrackerProperty:
    """Tests for BudgetEnforcer.cost_tracker property."""

    def test_returns_injected_tracker(self) -> None:
        """Property returns the same tracker injected at construction."""
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)
        assert enforcer.cost_tracker is tracker
