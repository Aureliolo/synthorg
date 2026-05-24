# mypy: disable-error-code="explicit-any"
"""Tests for BudgetEnforcer degradation integration."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

import pytest

from synthorg.budget.config import BudgetAlertConfig, BudgetConfig
from synthorg.budget.degradation import DegradationResult, PreFlightResult
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.errors import QuotaExhaustedError
from synthorg.budget.quota import (
    DegradationAction,
    DegradationConfig,
    QuotaLimit,
    QuotaWindow,
    SubscriptionConfig,
)
from synthorg.budget.quota_tracker import QuotaTracker
from synthorg.budget.tracker import CostTracker

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
    providers: dict[str, int] | None = None,
) -> QuotaTracker:
    """Build a QuotaTracker with per-hour request quotas."""
    if providers is None:
        providers = {"test-provider": 60}
    subs: dict[str, SubscriptionConfig] = {}
    for name, max_req in providers.items():
        subs[name] = SubscriptionConfig(
            quotas=(
                QuotaLimit(
                    window=QuotaWindow.PER_HOUR,
                    max_requests=max_req,
                ),
            ),
        )
    return QuotaTracker(subscriptions=subs)


async def _exhaust_provider(
    tracker: QuotaTracker,
    provider: str,
    count: int,
) -> None:
    for _ in range(count):
        await tracker.record_usage(provider)


def _patch_periods() -> tuple[
    AbstractContextManager[Any],
    AbstractContextManager[Any],
]:
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


# ── Tests ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestEnforcerFallback:
    """Tests for FALLBACK degradation through the enforcer."""

    async def test_fallback_returns_preflight_result(self) -> None:
        """Enforcer returns PreFlightResult with effective provider."""
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)
        quota_tracker = _make_quota_tracker(
            {
                "primary": 5,
                "fallback-a": 100,
            }
        )
        await _exhaust_provider(quota_tracker, "primary", 5)

        degradation_configs = {
            "primary": DegradationConfig(
                strategy=DegradationAction.FALLBACK,
                fallback_providers=("fallback-a",),
            ),
        }

        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            quota_tracker=quota_tracker,
            degradation_configs=degradation_configs,
        )

        billing_patch, daily_patch = _patch_periods()
        with billing_patch, daily_patch:
            result = await enforcer.check_can_execute(
                "alice",
                provider_name="primary",
            )

        assert isinstance(result, PreFlightResult)
        assert result.effective_provider == "fallback-a"
        assert result.degradation is not None
        assert result.degradation.action_taken == DegradationAction.FALLBACK


@pytest.mark.unit
class TestEnforcerQueue:
    """Tests for QUEUE degradation through the enforcer."""

    async def test_queue_returns_preflight_result(self) -> None:
        """Enforcer returns PreFlightResult after queue wait."""
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)
        quota_tracker = _make_quota_tracker({"primary": 5})
        await _exhaust_provider(quota_tracker, "primary", 5)

        degradation_configs = {
            "primary": DegradationConfig(
                strategy=DegradationAction.QUEUE,
                queue_max_wait_seconds=300,
            ),
        }

        mock_result = DegradationResult(
            original_provider="primary",
            effective_provider="primary",
            action_taken=DegradationAction.QUEUE,
            wait_seconds=30.0,
        )

        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            quota_tracker=quota_tracker,
            degradation_configs=degradation_configs,
        )

        billing_patch, daily_patch = _patch_periods()
        with (
            billing_patch,
            daily_patch,
            patch(
                "synthorg.budget.enforcer.resolve_degradation",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            result = await enforcer.check_can_execute(
                "alice",
                provider_name="primary",
            )

        assert isinstance(result, PreFlightResult)
        assert result.effective_provider == "primary"
        assert result.degradation is not None
        assert result.degradation.action_taken == DegradationAction.QUEUE
        assert result.degradation.wait_seconds == 30.0


@pytest.mark.unit
class TestEnforcerAlert:
    """Tests for ALERT (default) degradation through the enforcer."""

    async def test_alert_raises_as_before(self) -> None:
        """Default ALERT strategy raises QuotaExhaustedError."""
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)
        quota_tracker = _make_quota_tracker({"primary": 5})
        await _exhaust_provider(quota_tracker, "primary", 5)

        # No degradation configs -- defaults to ALERT
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
                provider_name="primary",
            )

    async def test_explicit_alert_config_raises(self) -> None:
        """Explicit ALERT config raises QuotaExhaustedError."""
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)
        quota_tracker = _make_quota_tracker({"primary": 5})
        await _exhaust_provider(quota_tracker, "primary", 5)

        degradation_configs = {
            "primary": DegradationConfig(
                strategy=DegradationAction.ALERT,
            ),
        }

        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            quota_tracker=quota_tracker,
            degradation_configs=degradation_configs,
        )

        billing_patch, daily_patch = _patch_periods()
        with (
            billing_patch,
            daily_patch,
            pytest.raises(QuotaExhaustedError),
        ):
            await enforcer.check_can_execute(
                "alice",
                provider_name="primary",
            )


@pytest.mark.unit
class TestEnforcerPreFlightResult:
    """Tests for PreFlightResult semantics."""

    async def test_no_provider_name_returns_empty_result(self) -> None:
        """No provider_name means no quota check, empty PreFlightResult."""
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)

        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
        )

        billing_patch, daily_patch = _patch_periods()
        with billing_patch, daily_patch:
            result = await enforcer.check_can_execute("alice")

        assert isinstance(result, PreFlightResult)
        assert result.effective_provider is None
        assert result.degradation is None

    async def test_allowed_provider_returns_empty_degradation(self) -> None:
        """Allowed provider returns PreFlightResult with no degradation."""
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)
        quota_tracker = _make_quota_tracker({"primary": 100})

        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            quota_tracker=quota_tracker,
        )

        billing_patch, daily_patch = _patch_periods()
        with billing_patch, daily_patch:
            result = await enforcer.check_can_execute(
                "alice",
                provider_name="primary",
            )

        assert isinstance(result, PreFlightResult)
        assert result.degradation is None

    async def test_missing_provider_config_uses_alert(self) -> None:
        """Provider without degradation config defaults to ALERT."""
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)
        quota_tracker = _make_quota_tracker({"primary": 5})
        await _exhaust_provider(quota_tracker, "primary", 5)

        # Degradation configs exist, but not for "primary"
        degradation_configs = {
            "other-provider": DegradationConfig(
                strategy=DegradationAction.FALLBACK,
                fallback_providers=("fallback-a",),
            ),
        }

        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            quota_tracker=quota_tracker,
            degradation_configs=degradation_configs,
        )

        billing_patch, daily_patch = _patch_periods()
        with (
            billing_patch,
            daily_patch,
            pytest.raises(QuotaExhaustedError),
        ):
            await enforcer.check_can_execute(
                "alice",
                provider_name="primary",
            )

    async def test_degradation_error_raises_quota_exhausted(self) -> None:
        """Unexpected error in degradation raises QuotaExhaustedError.

        After quota is confirmed denied, unexpected errors during
        degradation resolution are wrapped as QuotaExhaustedError
        (not swallowed).
        """
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)
        quota_tracker = _make_quota_tracker({"primary": 5})
        await _exhaust_provider(quota_tracker, "primary", 5)

        degradation_configs = {
            "primary": DegradationConfig(
                strategy=DegradationAction.FALLBACK,
                fallback_providers=("fallback-a",),
            ),
        }

        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=tracker,
            quota_tracker=quota_tracker,
            degradation_configs=degradation_configs,
        )

        billing_patch, daily_patch = _patch_periods()
        with (
            billing_patch,
            daily_patch,
            patch(
                "synthorg.budget.enforcer.resolve_degradation",
                new_callable=AsyncMock,
                side_effect=RuntimeError("unexpected"),
            ),
            pytest.raises(
                QuotaExhaustedError,
                match="Degradation resolution failed",
            ),
        ):
            await enforcer.check_can_execute(
                "alice",
                provider_name="primary",
            )
