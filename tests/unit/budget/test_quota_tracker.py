"""Tests for QuotaTracker service."""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from ai_company.budget.quota import (
    QuotaLimit,
    QuotaWindow,
    SubscriptionConfig,
)
from ai_company.budget.quota_tracker import QuotaTracker

pytestmark = pytest.mark.timeout(30)

# Fixed timestamps for deterministic tests
_NOW = datetime(2026, 3, 15, 14, 30, 0, tzinfo=UTC)
_MINUTE_START = datetime(2026, 3, 15, 14, 30, tzinfo=UTC)
_HOUR_START = datetime(2026, 3, 15, 14, 0, tzinfo=UTC)
_DAY_START = datetime(2026, 3, 15, tzinfo=UTC)
_MONTH_START = datetime(2026, 3, 1, tzinfo=UTC)


# ── Helpers ────────────────────────────────────────────────────────


def _make_tracker(
    *,
    provider: str = "test-provider",
    quotas: tuple[QuotaLimit, ...] = (),
    **kwargs: object,
) -> QuotaTracker:
    """Create a QuotaTracker with a single provider."""
    sub = SubscriptionConfig(quotas=quotas, **kwargs)  # type: ignore[arg-type]
    return QuotaTracker(subscriptions={provider: sub})


def _minute_quota(max_requests: int = 60) -> QuotaLimit:
    return QuotaLimit(window=QuotaWindow.PER_MINUTE, max_requests=max_requests)


def _day_token_quota(max_tokens: int = 1_000_000) -> QuotaLimit:
    return QuotaLimit(window=QuotaWindow.PER_DAY, max_tokens=max_tokens)


# ── Construction ───────────────────────────────────────────────────


@pytest.mark.unit
class TestQuotaTrackerConstruction:
    """Tests for QuotaTracker initialization."""

    def test_creates_with_empty_subscriptions(self) -> None:
        """Tracker with no subscriptions creates successfully."""
        tracker = QuotaTracker(subscriptions={})
        assert tracker is not None

    def test_creates_with_provider(self) -> None:
        """Tracker with provider subscription creates successfully."""
        tracker = _make_tracker(
            quotas=(_minute_quota(),),
        )
        assert tracker is not None

    def test_provider_without_quotas_not_tracked(self) -> None:
        """Provider with no quotas is not actively tracked."""
        sub = SubscriptionConfig()  # No quotas
        tracker = QuotaTracker(subscriptions={"test-provider": sub})
        # Should not raise or track
        assert tracker is not None


# ── record_usage ───────────────────────────────────────────────────


@pytest.mark.unit
class TestRecordUsage:
    """Tests for QuotaTracker.record_usage()."""

    async def test_records_request(self) -> None:
        """Records a single request."""
        tracker = _make_tracker(quotas=(_minute_quota(60),))

        await tracker.record_usage("test-provider")

        snapshots = await tracker.get_snapshot("test-provider")
        assert len(snapshots) == 1
        assert snapshots[0].requests_used == 1

    async def test_records_tokens(self) -> None:
        """Records token usage."""
        tracker = _make_tracker(quotas=(_day_token_quota(1_000_000),))

        await tracker.record_usage("test-provider", tokens=5000)

        snapshots = await tracker.get_snapshot("test-provider")
        assert len(snapshots) == 1
        assert snapshots[0].tokens_used == 5000

    async def test_accumulates_usage(self) -> None:
        """Multiple records accumulate within same window."""
        tracker = _make_tracker(quotas=(_minute_quota(60),))

        await tracker.record_usage("test-provider", requests=3)
        await tracker.record_usage("test-provider", requests=2)

        snapshots = await tracker.get_snapshot("test-provider")
        assert snapshots[0].requests_used == 5

    async def test_unknown_provider_is_noop(self) -> None:
        """Recording for unknown provider does nothing."""
        tracker = _make_tracker(quotas=(_minute_quota(),))

        # Should not raise
        await tracker.record_usage("unknown-provider")

    async def test_window_rotation(self) -> None:
        """Counters reset when window boundary is crossed."""
        # Use per_hour to avoid minute-boundary flakiness
        hour_quota = QuotaLimit(
            window=QuotaWindow.PER_HOUR,
            max_requests=60,
        )
        tracker = _make_tracker(quotas=(hour_quota,))

        # Record 10 requests (same hour as tracker creation)
        await tracker.record_usage("test-provider", requests=10)

        # Verify initial count
        snapshots = await tracker.get_snapshot(
            "test-provider",
            window=QuotaWindow.PER_HOUR,
        )
        assert snapshots[0].requests_used == 10

        # Force window rotation by mocking time to next hour
        next_hour = datetime(2099, 1, 1, 1, 0, 0, tzinfo=UTC)
        with patch(
            "ai_company.budget.quota_tracker.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = next_hour
            mock_dt.side_effect = datetime
            await tracker.record_usage("test-provider", requests=1)

            # Query in same mocked time so window matches
            snapshots = await tracker.get_snapshot(
                "test-provider",
                window=QuotaWindow.PER_HOUR,
            )

        # After rotation, only the new request counts
        assert snapshots[0].requests_used == 1


# ── check_quota ────────────────────────────────────────────────────


@pytest.mark.unit
class TestCheckQuota:
    """Tests for QuotaTracker.check_quota()."""

    async def test_allowed_when_under_limit(self) -> None:
        """Check passes when under quota limit."""
        tracker = _make_tracker(quotas=(_minute_quota(60),))

        await tracker.record_usage("test-provider", requests=30)

        result = await tracker.check_quota("test-provider")
        assert result.allowed is True

    async def test_denied_when_at_limit(self) -> None:
        """Check denied when at quota limit."""
        tracker = _make_tracker(quotas=(_minute_quota(10),))

        await tracker.record_usage("test-provider", requests=10)

        result = await tracker.check_quota("test-provider")
        assert result.allowed is False
        assert QuotaWindow.PER_MINUTE in result.exhausted_windows
        assert "requests" in result.reason

    async def test_denied_when_over_limit(self) -> None:
        """Check denied when over quota limit."""
        tracker = _make_tracker(quotas=(_minute_quota(10),))

        await tracker.record_usage("test-provider", requests=15)

        result = await tracker.check_quota("test-provider")
        assert result.allowed is False

    async def test_unknown_provider_always_allowed(self) -> None:
        """Unknown providers are always allowed."""
        tracker = _make_tracker(quotas=(_minute_quota(),))

        result = await tracker.check_quota("unknown-provider")
        assert result.allowed is True

    async def test_estimated_tokens_checked(self) -> None:
        """Estimated tokens are considered in quota check."""
        tracker = _make_tracker(quotas=(_day_token_quota(1000),))

        await tracker.record_usage("test-provider", tokens=800)

        # 800 used + 300 estimated = 1100 >= 1000
        result = await tracker.check_quota(
            "test-provider",
            estimated_tokens=300,
        )
        assert result.allowed is False

    async def test_estimated_tokens_under_limit_allowed(self) -> None:
        """Estimated tokens under limit passes."""
        tracker = _make_tracker(quotas=(_day_token_quota(1000),))

        await tracker.record_usage("test-provider", tokens=500)

        # 500 used + 100 estimated = 600 < 1000
        result = await tracker.check_quota(
            "test-provider",
            estimated_tokens=100,
        )
        assert result.allowed is True

    async def test_multiple_windows_checked(self) -> None:
        """All configured windows are checked."""
        tracker = _make_tracker(
            quotas=(
                _minute_quota(100),
                _day_token_quota(10_000),
            ),
        )

        # Exhaust daily tokens
        await tracker.record_usage("test-provider", requests=5, tokens=10_000)

        result = await tracker.check_quota("test-provider")
        assert result.allowed is False
        assert QuotaWindow.PER_DAY in result.exhausted_windows

    async def test_rotated_window_resets_check(self) -> None:
        """Rotated window allows requests again."""
        # Use per_hour to avoid minute-boundary flakiness
        hour_quota = QuotaLimit(
            window=QuotaWindow.PER_HOUR,
            max_requests=10,
        )
        tracker = _make_tracker(quotas=(hour_quota,))

        # Exhaust quota
        await tracker.record_usage("test-provider", requests=10)

        # Verify exhausted
        result = await tracker.check_quota("test-provider")
        assert result.allowed is False

        # Check in next hour (window rotated — check sees fresh window)
        next_hour = datetime(2099, 1, 1, 1, 0, 0, tzinfo=UTC)
        with patch(
            "ai_company.budget.quota_tracker.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = next_hour
            mock_dt.side_effect = datetime
            result = await tracker.check_quota("test-provider")

        assert result.allowed is True


# ── get_snapshot ───────────────────────────────────────────────────


@pytest.mark.unit
class TestGetSnapshot:
    """Tests for QuotaTracker.get_snapshot()."""

    async def test_returns_snapshots_for_tracked_provider(self) -> None:
        """Returns snapshots for tracked provider."""
        tracker = _make_tracker(quotas=(_minute_quota(60),))

        await tracker.record_usage("test-provider", requests=5)

        snapshots = await tracker.get_snapshot("test-provider")
        assert len(snapshots) == 1
        assert snapshots[0].provider_name == "test-provider"
        assert snapshots[0].window == QuotaWindow.PER_MINUTE
        assert snapshots[0].requests_used == 5
        assert snapshots[0].requests_limit == 60

    async def test_returns_empty_for_unknown_provider(self) -> None:
        """Returns empty tuple for unknown provider."""
        tracker = _make_tracker(quotas=(_minute_quota(),))

        snapshots = await tracker.get_snapshot("unknown")
        assert snapshots == ()

    async def test_filter_by_window(self) -> None:
        """Can filter snapshots by specific window."""
        tracker = _make_tracker(
            quotas=(
                _minute_quota(60),
                _day_token_quota(1_000_000),
            ),
        )

        snapshots = await tracker.get_snapshot(
            "test-provider",
            window=QuotaWindow.PER_MINUTE,
        )
        assert len(snapshots) == 1
        assert snapshots[0].window == QuotaWindow.PER_MINUTE

    async def test_rotated_window_shows_zero(self) -> None:
        """Rotated window shows zero usage in snapshot."""
        # Use per_hour to avoid minute-boundary flakiness
        hour_quota = QuotaLimit(
            window=QuotaWindow.PER_HOUR,
            max_requests=60,
        )
        tracker = _make_tracker(quotas=(hour_quota,))

        # Record requests
        await tracker.record_usage("test-provider", requests=10)

        # Query in next hour
        next_hour = datetime(2099, 1, 1, 1, 0, 0, tzinfo=UTC)
        with patch(
            "ai_company.budget.quota_tracker.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = next_hour
            mock_dt.side_effect = datetime
            snapshots = await tracker.get_snapshot("test-provider")

        assert snapshots[0].requests_used == 0


# ── get_all_snapshots ──────────────────────────────────────────────


@pytest.mark.unit
class TestGetAllSnapshots:
    """Tests for QuotaTracker.get_all_snapshots()."""

    async def test_returns_all_providers(self) -> None:
        """Returns snapshots for all tracked providers."""
        sub_a = SubscriptionConfig(
            quotas=(_minute_quota(60),),
        )
        sub_b = SubscriptionConfig(
            quotas=(_day_token_quota(1_000_000),),
        )
        tracker = QuotaTracker(
            subscriptions={"provider-a": sub_a, "provider-b": sub_b},
        )

        all_snapshots = await tracker.get_all_snapshots()
        assert "provider-a" in all_snapshots
        assert "provider-b" in all_snapshots

    async def test_empty_when_no_subscriptions(self) -> None:
        """Returns empty dict when no subscriptions."""
        tracker = QuotaTracker(subscriptions={})
        all_snapshots = await tracker.get_all_snapshots()
        assert all_snapshots == {}
