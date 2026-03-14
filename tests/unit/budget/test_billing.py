"""Tests for billing period computation utilities."""

from datetime import UTC, datetime

import pytest

from synthorg.budget.billing import billing_period_start, daily_period_start

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestBillingPeriodStart:
    """Tests for billing_period_start()."""

    @pytest.mark.parametrize(
        ("reset_day", "now", "expected"),
        [
            # Same month: now.day >= reset_day
            (
                1,
                datetime(2026, 3, 15, 10, 30, tzinfo=UTC),
                datetime(2026, 3, 1, tzinfo=UTC),
            ),
            (
                15,
                datetime(2026, 3, 20, 8, 0, tzinfo=UTC),
                datetime(2026, 3, 15, tzinfo=UTC),
            ),
            # Exact boundary: now.day == reset_day
            (
                1,
                datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
                datetime(2026, 1, 1, tzinfo=UTC),
            ),
            (
                15,
                datetime(2026, 3, 15, 23, 59, tzinfo=UTC),
                datetime(2026, 3, 15, tzinfo=UTC),
            ),
            # Previous month: now.day < reset_day
            (
                15,
                datetime(2026, 3, 10, 12, 0, tzinfo=UTC),
                datetime(2026, 2, 15, tzinfo=UTC),
            ),
            (
                10,
                datetime(2026, 3, 5, 0, 0, tzinfo=UTC),
                datetime(2026, 2, 10, tzinfo=UTC),
            ),
            # Year boundary: January with rollback to December
            (
                10,
                datetime(2026, 1, 5, 0, 0, tzinfo=UTC),
                datetime(2025, 12, 10, tzinfo=UTC),
            ),
            # reset_day=28 (max allowed), February is safe
            (
                28,
                datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
                datetime(2026, 2, 28, tzinfo=UTC),
            ),
            # reset_day=1 always stays in current month
            (
                1,
                datetime(2026, 12, 31, 23, 59, tzinfo=UTC),
                datetime(2026, 12, 1, tzinfo=UTC),
            ),
        ],
        ids=[
            "same_month_day1",
            "same_month_day15",
            "exact_boundary_jan1",
            "exact_boundary_day15",
            "prev_month_day15",
            "prev_month_day10",
            "year_boundary",
            "feb_28_safe",
            "dec_31_day1",
        ],
    )
    def test_billing_period_start(
        self,
        reset_day: int,
        now: datetime,
        expected: datetime,
    ) -> None:
        """Verify billing period start for various date/reset_day combos."""
        result = billing_period_start(reset_day, now=now)
        assert result == expected

    def test_defaults_to_utc_now(self) -> None:
        """Verify billing_period_start works without explicit now."""
        result = billing_period_start(1)
        assert result.tzinfo is UTC
        assert result.hour == 0
        assert result.minute == 0
        assert result.second == 0

    def test_result_is_utc_aware(self) -> None:
        """Result always has UTC timezone."""
        result = billing_period_start(
            15,
            now=datetime(2026, 3, 20, tzinfo=UTC),
        )
        assert result.tzinfo is UTC

    @pytest.mark.parametrize(
        "invalid_day",
        [0, -1, 29, 31, 100],
        ids=["zero", "negative", "29", "31", "100"],
    )
    def test_invalid_reset_day_raises(self, invalid_day: int) -> None:
        """Invalid reset_day raises ValueError."""
        with pytest.raises(ValueError, match="reset_day must be 1-28"):
            billing_period_start(invalid_day)


@pytest.mark.unit
class TestDailyPeriodStart:
    """Tests for daily_period_start()."""

    def test_returns_midnight_utc(self) -> None:
        """Verify midnight UTC of the given day."""
        now = datetime(2026, 3, 15, 14, 30, 45, tzinfo=UTC)
        result = daily_period_start(now=now)
        assert result == datetime(2026, 3, 15, tzinfo=UTC)

    def test_already_at_midnight(self) -> None:
        """When now is already midnight, return same instant."""
        now = datetime(2026, 3, 15, 0, 0, 0, tzinfo=UTC)
        result = daily_period_start(now=now)
        assert result == now

    def test_defaults_to_utc_now(self) -> None:
        """Verify daily_period_start works without explicit now."""
        result = daily_period_start()
        assert result.tzinfo is UTC
        assert result.hour == 0
        assert result.minute == 0
        assert result.second == 0
