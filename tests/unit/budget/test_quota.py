"""Tests for quota and subscription domain models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.budget.quota import (
    DegradationAction,
    DegradationConfig,
    ProviderCostModel,
    QuotaCheckResult,
    QuotaLimit,
    QuotaSnapshot,
    QuotaWindow,
    SubscriptionConfig,
    effective_cost_per_1k,
    window_start,
)

pytestmark = pytest.mark.timeout(30)


# ── QuotaWindow ────────────────────────────────────────────────────


@pytest.mark.unit
class TestQuotaWindow:
    """Tests for QuotaWindow enum."""

    def test_values(self) -> None:
        """All expected windows exist."""
        assert QuotaWindow.PER_MINUTE.value == "per_minute"
        assert QuotaWindow.PER_HOUR.value == "per_hour"
        assert QuotaWindow.PER_DAY.value == "per_day"
        assert QuotaWindow.PER_MONTH.value == "per_month"

    def test_member_count(self) -> None:
        """Exactly 4 windows."""
        assert len(QuotaWindow) == 4


# ── QuotaLimit ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestQuotaLimit:
    """Tests for QuotaLimit model."""

    def test_valid_requests_only(self) -> None:
        """Limit with only max_requests is valid."""
        ql = QuotaLimit(window=QuotaWindow.PER_MINUTE, max_requests=60)
        assert ql.max_requests == 60
        assert ql.max_tokens == 0

    def test_valid_tokens_only(self) -> None:
        """Limit with only max_tokens is valid."""
        ql = QuotaLimit(window=QuotaWindow.PER_DAY, max_tokens=1_000_000)
        assert ql.max_tokens == 1_000_000
        assert ql.max_requests == 0

    def test_both_set(self) -> None:
        """Limit with both fields set is valid."""
        ql = QuotaLimit(
            window=QuotaWindow.PER_HOUR,
            max_requests=100,
            max_tokens=500_000,
        )
        assert ql.max_requests == 100
        assert ql.max_tokens == 500_000

    def test_both_zero_rejected(self) -> None:
        """Both at zero is rejected."""
        with pytest.raises(ValidationError, match="At least one"):
            QuotaLimit(window=QuotaWindow.PER_MINUTE)

    def test_negative_requests_rejected(self) -> None:
        """Negative max_requests is rejected."""
        with pytest.raises(ValidationError):
            QuotaLimit(
                window=QuotaWindow.PER_MINUTE,
                max_requests=-1,
            )

    def test_negative_tokens_rejected(self) -> None:
        """Negative max_tokens is rejected."""
        with pytest.raises(ValidationError):
            QuotaLimit(
                window=QuotaWindow.PER_MINUTE,
                max_tokens=-1,
            )

    def test_frozen(self) -> None:
        """Model is frozen."""
        ql = QuotaLimit(window=QuotaWindow.PER_MINUTE, max_requests=10)
        with pytest.raises(ValidationError):
            ql.max_requests = 20  # type: ignore[misc]


# ── ProviderCostModel ──────────────────────────────────────────────


@pytest.mark.unit
class TestProviderCostModel:
    """Tests for ProviderCostModel enum."""

    def test_values(self) -> None:
        """All expected cost models exist."""
        assert ProviderCostModel.PER_TOKEN.value == "per_token"
        assert ProviderCostModel.SUBSCRIPTION.value == "subscription"
        assert ProviderCostModel.LOCAL.value == "local"


# ── SubscriptionConfig ─────────────────────────────────────────────


@pytest.mark.unit
class TestSubscriptionConfig:
    """Tests for SubscriptionConfig model."""

    def test_defaults(self) -> None:
        """Default config is pay-as-you-go."""
        sc = SubscriptionConfig()
        assert sc.plan_name == "pay_as_you_go"
        assert sc.cost_model == ProviderCostModel.PER_TOKEN
        assert sc.monthly_cost == 0.0
        assert sc.quotas == ()
        assert sc.hardware_limits is None

    def test_subscription_with_monthly_cost(self) -> None:
        """Subscription model with monthly cost."""
        sc = SubscriptionConfig(
            plan_name="pro",
            cost_model=ProviderCostModel.SUBSCRIPTION,
            monthly_cost=20.0,
        )
        assert sc.monthly_cost == 20.0

    def test_local_with_hardware_limits(self) -> None:
        """Local model with hardware limits."""
        sc = SubscriptionConfig(
            plan_name="local",
            cost_model=ProviderCostModel.LOCAL,
            hardware_limits="RTX 4090, ~30 tok/s",
        )
        assert sc.hardware_limits == "RTX 4090, ~30 tok/s"

    def test_local_with_monthly_cost_rejected(self) -> None:
        """LOCAL cost_model with monthly_cost > 0 is rejected."""
        with pytest.raises(ValidationError, match="LOCAL cost_model"):
            SubscriptionConfig(
                cost_model=ProviderCostModel.LOCAL,
                monthly_cost=10.0,
            )

    def test_subscription_zero_monthly_cost_warns(self) -> None:
        """SUBSCRIPTION with monthly_cost=0 logs warning but is accepted."""
        # Should not raise — just warns
        sc = SubscriptionConfig(
            cost_model=ProviderCostModel.SUBSCRIPTION,
            monthly_cost=0.0,
        )
        assert sc.monthly_cost == 0.0

    def test_duplicate_quota_windows_rejected(self) -> None:
        """Duplicate quota windows are rejected."""
        with pytest.raises(ValidationError, match="Duplicate quota windows"):
            SubscriptionConfig(
                quotas=(
                    QuotaLimit(
                        window=QuotaWindow.PER_MINUTE,
                        max_requests=60,
                    ),
                    QuotaLimit(
                        window=QuotaWindow.PER_MINUTE,
                        max_requests=30,
                    ),
                ),
            )

    def test_multiple_unique_windows_accepted(self) -> None:
        """Multiple unique windows are accepted."""
        sc = SubscriptionConfig(
            quotas=(
                QuotaLimit(window=QuotaWindow.PER_MINUTE, max_requests=60),
                QuotaLimit(window=QuotaWindow.PER_DAY, max_tokens=1_000_000),
            ),
        )
        assert len(sc.quotas) == 2

    def test_negative_monthly_cost_rejected(self) -> None:
        """Negative monthly_cost is rejected."""
        with pytest.raises(ValidationError):
            SubscriptionConfig(monthly_cost=-10.0)

    def test_frozen(self) -> None:
        """Model is frozen."""
        sc = SubscriptionConfig()
        with pytest.raises(ValidationError):
            sc.plan_name = "other"  # type: ignore[misc]

    def test_blank_plan_name_rejected(self) -> None:
        """Blank plan_name is rejected."""
        with pytest.raises(ValidationError):
            SubscriptionConfig(plan_name="")


# ── DegradationAction ──────────────────────────────────────────────


@pytest.mark.unit
class TestDegradationAction:
    """Tests for DegradationAction enum."""

    def test_values(self) -> None:
        """All expected actions exist."""
        assert DegradationAction.FALLBACK.value == "fallback"
        assert DegradationAction.QUEUE.value == "queue"
        assert DegradationAction.ALERT.value == "alert"


# ── DegradationConfig ─────────────────────────────────────────────


@pytest.mark.unit
class TestDegradationConfig:
    """Tests for DegradationConfig model."""

    def test_defaults(self) -> None:
        """Default is ALERT with no fallback providers."""
        dc = DegradationConfig()
        assert dc.strategy == DegradationAction.ALERT
        assert dc.fallback_providers == ()
        assert dc.queue_max_wait_seconds == 300

    def test_alert_strategy(self) -> None:
        """ALERT strategy is accepted."""
        dc = DegradationConfig(strategy=DegradationAction.ALERT)
        assert dc.strategy == DegradationAction.ALERT

    def test_fallback_with_providers(self) -> None:
        """FALLBACK with providers is accepted."""
        dc = DegradationConfig(
            strategy=DegradationAction.FALLBACK,
            fallback_providers=("provider-a", "provider-b"),
        )
        assert len(dc.fallback_providers) == 2

    def test_fallback_without_providers_warns(self) -> None:
        """FALLBACK with empty providers logs warning but is accepted."""
        dc = DegradationConfig(strategy=DegradationAction.FALLBACK)
        assert dc.fallback_providers == ()

    def test_queue_max_wait_bounds(self) -> None:
        """queue_max_wait_seconds must be in [0, 3600]."""
        with pytest.raises(ValidationError):
            DegradationConfig(queue_max_wait_seconds=-1)
        with pytest.raises(ValidationError):
            DegradationConfig(queue_max_wait_seconds=3601)

    def test_frozen(self) -> None:
        """Model is frozen."""
        dc = DegradationConfig()
        with pytest.raises(ValidationError):
            dc.strategy = DegradationAction.ALERT  # type: ignore[misc]


# ── QuotaSnapshot ──────────────────────────────────────────────────


@pytest.mark.unit
class TestQuotaSnapshot:
    """Tests for QuotaSnapshot model."""

    def _make_snapshot(
        self,
        *,
        requests_used: int = 0,
        requests_limit: int = 100,
        tokens_used: int = 0,
        tokens_limit: int = 0,
    ) -> QuotaSnapshot:
        return QuotaSnapshot(
            provider_name="test-provider",
            window=QuotaWindow.PER_MINUTE,
            requests_used=requests_used,
            requests_limit=requests_limit,
            tokens_used=tokens_used,
            tokens_limit=tokens_limit,
            captured_at=datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC),
        )

    def test_requests_remaining(self) -> None:
        """Computes remaining requests correctly."""
        snap = self._make_snapshot(
            requests_used=30,
            requests_limit=100,
        )
        assert snap.requests_remaining == 70

    def test_requests_remaining_at_limit(self) -> None:
        """Remaining is 0 when at limit."""
        snap = self._make_snapshot(
            requests_used=100,
            requests_limit=100,
        )
        assert snap.requests_remaining == 0

    def test_requests_remaining_unlimited(self) -> None:
        """Remaining is None when limit is unlimited (0)."""
        snap = self._make_snapshot(
            requests_used=50,
            requests_limit=0,
        )
        assert snap.requests_remaining is None

    def test_tokens_remaining(self) -> None:
        """Computes remaining tokens correctly."""
        snap = self._make_snapshot(
            tokens_used=500,
            tokens_limit=1000,
        )
        assert snap.tokens_remaining == 500

    def test_tokens_remaining_unlimited(self) -> None:
        """Remaining is None when tokens unlimited."""
        snap = self._make_snapshot(
            tokens_used=500,
            tokens_limit=0,
        )
        assert snap.tokens_remaining is None

    def test_is_exhausted_requests(self) -> None:
        """Exhausted when requests at limit."""
        snap = self._make_snapshot(
            requests_used=100,
            requests_limit=100,
        )
        assert snap.is_exhausted is True

    def test_is_exhausted_tokens(self) -> None:
        """Exhausted when tokens at limit."""
        snap = self._make_snapshot(
            requests_limit=0,
            tokens_used=1000,
            tokens_limit=1000,
        )
        assert snap.is_exhausted is True

    def test_not_exhausted(self) -> None:
        """Not exhausted when under both limits."""
        snap = self._make_snapshot(
            requests_used=50,
            requests_limit=100,
            tokens_used=500,
            tokens_limit=1000,
        )
        assert snap.is_exhausted is False

    def test_not_exhausted_unlimited(self) -> None:
        """Not exhausted when both limits are unlimited."""
        snap = self._make_snapshot(
            requests_used=1000,
            requests_limit=0,
            tokens_used=1000,
            tokens_limit=0,
        )
        assert snap.is_exhausted is False


# ── QuotaCheckResult ───────────────────────────────────────────────


@pytest.mark.unit
class TestQuotaCheckResult:
    """Tests for QuotaCheckResult model."""

    def test_allowed(self) -> None:
        """Allowed result."""
        result = QuotaCheckResult(
            allowed=True,
            provider_name="test-provider",
        )
        assert result.allowed is True
        assert result.reason == ""
        assert result.exhausted_windows == ()

    def test_denied(self) -> None:
        """Denied result with reason and exhausted windows."""
        result = QuotaCheckResult(
            allowed=False,
            provider_name="test-provider",
            reason="per_minute requests exhausted",
            exhausted_windows=(QuotaWindow.PER_MINUTE,),
        )
        assert result.allowed is False
        assert "per_minute" in result.reason


# ── window_start ───────────────────────────────────────────────────


@pytest.mark.unit
class TestWindowStart:
    """Tests for window_start function."""

    def test_per_minute(self) -> None:
        """PER_MINUTE truncates to minute start."""
        now = datetime(2026, 3, 15, 14, 35, 42, tzinfo=UTC)
        result = window_start(QuotaWindow.PER_MINUTE, now=now)
        assert result == datetime(2026, 3, 15, 14, 35, tzinfo=UTC)

    def test_per_hour(self) -> None:
        """PER_HOUR truncates to hour start."""
        now = datetime(2026, 3, 15, 14, 35, 42, tzinfo=UTC)
        result = window_start(QuotaWindow.PER_HOUR, now=now)
        assert result == datetime(2026, 3, 15, 14, 0, tzinfo=UTC)

    def test_per_day(self) -> None:
        """PER_DAY truncates to day start."""
        now = datetime(2026, 3, 15, 14, 35, 42, tzinfo=UTC)
        result = window_start(QuotaWindow.PER_DAY, now=now)
        assert result == datetime(2026, 3, 15, tzinfo=UTC)

    def test_per_month(self) -> None:
        """PER_MONTH truncates to first of month."""
        now = datetime(2026, 3, 15, 14, 35, 42, tzinfo=UTC)
        result = window_start(QuotaWindow.PER_MONTH, now=now)
        assert result == datetime(2026, 3, 1, tzinfo=UTC)

    def test_naive_datetime_rejected(self) -> None:
        """Naive datetime raises ValueError."""
        naive = datetime(2026, 3, 15, 14, 30, 0)  # noqa: DTZ001
        with pytest.raises(ValueError, match="timezone-aware"):
            window_start(QuotaWindow.PER_HOUR, now=naive)

    def test_defaults_to_now(self) -> None:
        """Uses current time when now is not provided."""
        before = datetime.now(UTC)
        result = window_start(QuotaWindow.PER_MONTH)
        after = datetime.now(UTC)
        # PER_MONTH truncates to 1st of month — stable within a month
        assert result.year == before.year
        assert result.month == before.month
        assert result.day == 1
        assert before <= after  # sanity


# ── effective_cost_per_1k ──────────────────────────────────────────


@pytest.mark.unit
class TestEffectiveCostPer1k:
    """Tests for effective_cost_per_1k function."""

    def test_per_token(self) -> None:
        """PER_TOKEN returns sum of input + output costs."""
        result = effective_cost_per_1k(0.003, 0.015, ProviderCostModel.PER_TOKEN)
        assert result == 0.018

    def test_subscription_returns_zero(self) -> None:
        """SUBSCRIPTION returns 0.0 (pre-paid)."""
        result = effective_cost_per_1k(0.003, 0.015, ProviderCostModel.SUBSCRIPTION)
        assert result == 0.0

    def test_local_returns_zero(self) -> None:
        """LOCAL returns 0.0 (free)."""
        result = effective_cost_per_1k(0.003, 0.015, ProviderCostModel.LOCAL)
        assert result == 0.0

    def test_per_token_zero_costs(self) -> None:
        """PER_TOKEN with zero costs returns 0.0."""
        result = effective_cost_per_1k(0.0, 0.0, ProviderCostModel.PER_TOKEN)
        assert result == 0.0

    def test_per_token_negative_inputs(self) -> None:
        """PER_TOKEN with negative inputs returns the sum as-is."""
        result = effective_cost_per_1k(-0.001, 0.005, ProviderCostModel.PER_TOKEN)
        assert result == pytest.approx(0.004)


# ── SubscriptionConfig nan/inf rejection ──────────────────────────


@pytest.mark.unit
class TestSubscriptionConfigNanInf:
    """Tests that SubscriptionConfig rejects nan/inf values."""

    def test_rejects_nan_monthly_cost(self) -> None:
        """NaN monthly_cost is rejected."""
        with pytest.raises(ValidationError):
            SubscriptionConfig(monthly_cost=float("nan"))

    def test_rejects_inf_monthly_cost(self) -> None:
        """Inf monthly_cost is rejected."""
        with pytest.raises(ValidationError):
            SubscriptionConfig(monthly_cost=float("inf"))


# ── QuotaSnapshot over-limit ─────────────────────────────────────


@pytest.mark.unit
class TestQuotaSnapshotOverLimit:
    """Tests for QuotaSnapshot with usage exceeding limits."""

    def test_is_exhausted_over_limit(self) -> None:
        """is_exhausted returns True when usage exceeds limit."""
        snap = QuotaSnapshot(
            provider_name="test-provider",
            window=QuotaWindow.PER_MINUTE,
            requests_used=150,
            requests_limit=100,
            captured_at=datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC),
        )
        assert snap.is_exhausted is True
        assert snap.requests_remaining == 0


# ── QuotaCheckResult cross-field validation ───────────────────────


@pytest.mark.unit
class TestQuotaCheckResultValidation:
    """Tests for QuotaCheckResult cross-field validation."""

    def test_denied_without_reason_rejected(self) -> None:
        """Denied result with empty reason is rejected."""
        with pytest.raises(ValidationError, match="non-empty reason"):
            QuotaCheckResult(
                allowed=False,
                provider_name="test-provider",
            )

    def test_allowed_with_exhausted_windows_rejected(self) -> None:
        """Allowed result with exhausted_windows is rejected."""
        with pytest.raises(
            ValidationError,
            match="must not have exhausted_windows",
        ):
            QuotaCheckResult(
                allowed=True,
                provider_name="test-provider",
                exhausted_windows=(QuotaWindow.PER_MINUTE,),
            )
