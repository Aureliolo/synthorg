"""Tests for BudgetEnforcer quota integration."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

import pytest

from synthorg.budget.config import BudgetAlertConfig, BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.errors import QuotaExhaustedError
from synthorg.budget.quota import (
    QuotaCheckResult,
    QuotaLimit,
    QuotaWindow,
    SubscriptionConfig,
)
from synthorg.budget.quota_tracker import QuotaTracker
from synthorg.budget.tracker import CostTracker

pytestmark = pytest.mark.timeout(30)

_BILLING_START = datetime(2026, 3, 1, tzinfo=UTC)
_DAY_START = datetime(2026, 3, 15, tzinfo=UTC)


# ── Helpers ────────────────────────────────────────────────────────


def _make_budget_config(
    *,
    total_monthly: float = 100.0,
    per_agent_daily_limit: float = 10.0,
) -> BudgetConfig:
    return BudgetConfig(
        total_monthly=total_monthly,
        alerts=BudgetAlertConfig(warn_at=75, critical_at=90, hard_stop_at=100),
        per_agent_daily_limit=per_agent_daily_limit,
    )


def _make_quota_tracker(
    *,
    provider: str = "test-provider",
    max_requests: int = 60,
) -> QuotaTracker:
    sub = SubscriptionConfig(
        quotas=(
            QuotaLimit(
                window=QuotaWindow.PER_HOUR,
                max_requests=max_requests,
            ),
        ),
    )
    return QuotaTracker(subscriptions={provider: sub})


def _patch_periods() -> tuple[
    AbstractContextManager[Any],
    AbstractContextManager[Any],
]:
    """Patch billing and daily period starts."""
    return (
        patch(
            "synthorg.budget.enforcer.billing_period_start",
            return_value=_BILLING_START,
        ),
        patch(
            "synthorg.budget.enforcer.daily_period_start",
            return_value=_DAY_START,
        ),
    )


# ── check_can_execute with quota ───────────────────────────────────


@pytest.mark.unit
class TestCheckCanExecuteWithQuota:
    """Tests for quota-aware pre-flight check."""

    async def test_passes_when_quota_allowed(self) -> None:
        """Pre-flight passes when quota is not exhausted."""
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)
        quota_tracker = _make_quota_tracker()

        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            quota_tracker=quota_tracker,
        )

        billing_patch, daily_patch = _patch_periods()
        with billing_patch, daily_patch:
            await enforcer.check_can_execute(
                "alice",
                provider_name="test-provider",
            )

    async def test_raises_when_quota_exhausted(self) -> None:
        """Pre-flight raises QuotaExhaustedError when exhausted."""
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)
        quota_tracker = _make_quota_tracker(max_requests=5)

        # Exhaust quota
        for _ in range(5):
            await quota_tracker.record_usage("test-provider")

        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            quota_tracker=quota_tracker,
        )

        billing_patch, daily_patch = _patch_periods()
        with (
            billing_patch,
            daily_patch,
            pytest.raises(
                QuotaExhaustedError,
                match="quota exhausted",
            ),
        ):
            await enforcer.check_can_execute(
                "alice",
                provider_name="test-provider",
            )

    async def test_skips_quota_when_no_provider_name(self) -> None:
        """Quota check is skipped when provider_name is None."""
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)
        quota_tracker = _make_quota_tracker(max_requests=5)

        # Exhaust quota
        for _ in range(5):
            await quota_tracker.record_usage("test-provider")

        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            quota_tracker=quota_tracker,
        )

        billing_patch, daily_patch = _patch_periods()
        with billing_patch, daily_patch:
            # No provider_name → skip quota check, even though exhausted
            await enforcer.check_can_execute("alice")

    async def test_skips_quota_when_no_quota_tracker(self) -> None:
        """Quota check is skipped when no quota_tracker is set."""
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)

        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            quota_tracker=None,
        )

        billing_patch, daily_patch = _patch_periods()
        with billing_patch, daily_patch:
            await enforcer.check_can_execute(
                "alice",
                provider_name="test-provider",
            )


# ── check_quota ────────────────────────────────────────────────────


@pytest.mark.unit
class TestCheckQuota:
    """Tests for BudgetEnforcer.check_quota()."""

    async def test_delegates_to_quota_tracker(self) -> None:
        """Delegates to QuotaTracker when set."""
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)
        quota_tracker = _make_quota_tracker()

        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            quota_tracker=quota_tracker,
        )

        result = await enforcer.check_quota("test-provider")
        assert result.allowed is True
        assert result.provider_name == "test-provider"

    async def test_returns_allowed_without_quota_tracker(self) -> None:
        """Returns always-allowed when no quota tracker."""
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)

        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            quota_tracker=None,
        )

        result = await enforcer.check_quota("test-provider")
        assert result.allowed is True

    async def test_passes_estimated_tokens(self) -> None:
        """Estimated tokens are forwarded to QuotaTracker."""
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)
        quota_tracker = _make_quota_tracker()

        mock_check = AsyncMock(
            return_value=QuotaCheckResult(
                allowed=True,
                provider_name="test-provider",
            ),
        )
        quota_tracker.check_quota = mock_check  # type: ignore[method-assign]

        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            quota_tracker=quota_tracker,
        )

        await enforcer.check_quota(
            "test-provider",
            estimated_tokens=5000,
        )

        mock_check.assert_awaited_once_with(
            "test-provider",
            estimated_tokens=5000,
        )

    async def test_quota_exhausted_returns_denied(self) -> None:
        """Returns denied result when quota is exhausted."""
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)
        quota_tracker = _make_quota_tracker(max_requests=2)

        # Exhaust quota
        await quota_tracker.record_usage("test-provider", requests=2)

        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            quota_tracker=quota_tracker,
        )

        result = await enforcer.check_quota("test-provider")
        assert result.allowed is False
        assert result.provider_name == "test-provider"

    async def test_graceful_degradation_on_generic_exception(self) -> None:
        """Falls back to allow when quota_tracker raises unexpectedly."""
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)
        quota_tracker = _make_quota_tracker()

        # Mock check_quota to raise a generic error
        quota_tracker.check_quota = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("unexpected"),
        )

        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            quota_tracker=quota_tracker,
        )

        billing_patch, daily_patch = _patch_periods()
        with billing_patch, daily_patch:
            # Should not raise — graceful degradation
            await enforcer.check_can_execute(
                "alice",
                provider_name="test-provider",
            )
