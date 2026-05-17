"""Tests for ClassificationBudgetTracker."""

import asyncio

import pytest

from synthorg.engine.classification.budget_tracker import (
    ClassificationBudgetTracker,
)


@pytest.mark.unit
class TestClassificationBudgetTracker:
    """ClassificationBudgetTracker spend tracking."""

    def test_initial_state(self) -> None:
        tracker = ClassificationBudgetTracker(budget=0.10)
        assert tracker.remaining == 0.10
        assert tracker.total_spent == 0.0

    async def test_try_reserve_within_budget(self) -> None:
        tracker = ClassificationBudgetTracker(budget=0.10)
        assert await tracker.try_reserve(0.05) is True
        assert tracker.total_spent == pytest.approx(0.05)

    async def test_try_reserve_exact_budget(self) -> None:
        tracker = ClassificationBudgetTracker(budget=0.10)
        assert await tracker.try_reserve(0.10) is True
        assert tracker.remaining == 0.0

    async def test_try_reserve_over_budget(self) -> None:
        tracker = ClassificationBudgetTracker(budget=0.10)
        assert await tracker.try_reserve(0.11) is False
        assert tracker.total_spent == 0.0

    async def test_reserve_settle_reduces_remaining(self) -> None:
        tracker = ClassificationBudgetTracker(budget=0.10)
        assert await tracker.try_reserve(0.03) is True
        await tracker.settle(0.03, 0.03)
        assert tracker.total_spent == pytest.approx(0.03)
        assert tracker.remaining == pytest.approx(0.07)

    async def test_settled_spend_then_cannot_reserve(self) -> None:
        tracker = ClassificationBudgetTracker(budget=0.10)
        assert await tracker.try_reserve(0.08) is True
        await tracker.settle(0.08, 0.08)
        assert await tracker.try_reserve(0.05) is False
        # The failed reserve must not leak into spent state.
        assert tracker.total_spent == pytest.approx(0.08)
        assert await tracker.try_reserve(0.02) is True

    async def test_remaining_never_negative_on_overspend(self) -> None:
        tracker = ClassificationBudgetTracker(budget=0.10)
        assert await tracker.try_reserve(0.05) is True
        # Actual cost overran the estimate and the whole budget.
        await tracker.settle(0.05, 0.15)
        assert tracker.total_spent == pytest.approx(0.15)
        assert tracker.remaining == 0.0

    async def test_zero_budget(self) -> None:
        tracker = ClassificationBudgetTracker(budget=0.0)
        assert await tracker.try_reserve(0.0) is True
        assert await tracker.try_reserve(0.001) is False

    def test_negative_budget_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            ClassificationBudgetTracker(budget=-0.01)

    async def test_negative_estimated_cost_rejected(self) -> None:
        tracker = ClassificationBudgetTracker(budget=0.10)
        with pytest.raises(ValueError, match="non-negative"):
            await tracker.try_reserve(-0.01)

    async def test_negative_actual_cost_rejected(self) -> None:
        tracker = ClassificationBudgetTracker(budget=0.10)
        with pytest.raises(ValueError, match="non-negative"):
            await tracker.settle(0.0, -0.01)

    async def test_multiple_settled_spends(self) -> None:
        tracker = ClassificationBudgetTracker(budget=0.10)
        for cost in (0.02, 0.03, 0.01):
            assert await tracker.try_reserve(cost) is True
            await tracker.settle(cost, cost)
        assert tracker.total_spent == pytest.approx(0.06)
        assert tracker.remaining == pytest.approx(0.04)

    async def test_concurrent_settles_are_lock_safe(self) -> None:
        """Concurrent reserve+settle pairs must accumulate without loss."""
        tracker = ClassificationBudgetTracker(budget=10.0)

        async def spend(cost: float) -> None:
            assert await tracker.try_reserve(cost) is True
            await tracker.settle(cost, cost)

        await asyncio.gather(*(spend(0.01) for _ in range(100)))
        assert tracker.total_spent == pytest.approx(1.0)
        assert tracker.remaining == pytest.approx(9.0)

    async def test_concurrent_reserves_respect_budget(self) -> None:
        """Concurrent try_reserve calls must never overspend the budget.

        This is the core property the atomic admission pattern
        fixes versus the old split ``can_spend`` + ``record`` API:
        only the lucky winners may reserve and total spend is
        capped at the configured budget regardless of concurrency.
        """
        tracker = ClassificationBudgetTracker(budget=1.0)
        # Each reservation asks for 0.07 units; the budget admits
        # exactly 14 of them (14 * 0.07 = 0.98, 15 * 0.07 > 1.00).
        reserve_cost = 0.07
        total_attempts = 200
        expected_reservations = 14

        results = await asyncio.gather(
            *(tracker.try_reserve(reserve_cost) for _ in range(total_attempts)),
        )
        successes = sum(1 for r in results if r)
        assert successes == expected_reservations
        assert tracker.total_spent == pytest.approx(
            expected_reservations * reserve_cost,
        )
        # No overshoot under any scheduling.
        assert tracker.total_spent <= 1.0

    async def test_settle_applies_delta(self) -> None:
        """settle() reconciles an estimated reservation with the actual cost."""
        tracker = ClassificationBudgetTracker(budget=0.10)
        assert await tracker.try_reserve(0.05) is True
        # Actual came in cheaper -- remaining must grow back.
        await tracker.settle(estimated_cost=0.05, actual_cost=0.03)
        assert tracker.total_spent == pytest.approx(0.03)
        assert tracker.remaining == pytest.approx(0.07)

    async def test_settle_positive_delta(self) -> None:
        """A more-expensive-than-expected actual cost increases spend."""
        tracker = ClassificationBudgetTracker(budget=0.10)
        assert await tracker.try_reserve(0.03) is True
        await tracker.settle(estimated_cost=0.03, actual_cost=0.05)
        assert tracker.total_spent == pytest.approx(0.05)

    async def test_release_refunds_reservation(self) -> None:
        """release() returns a reservation that never incurred cost."""
        tracker = ClassificationBudgetTracker(budget=0.10)
        assert await tracker.try_reserve(0.04) is True
        assert tracker.total_spent == pytest.approx(0.04)
        await tracker.release(0.04)
        assert tracker.total_spent == pytest.approx(0.0)
        assert tracker.remaining == pytest.approx(0.10)

    async def test_release_clamps_at_zero(self) -> None:
        """release() never drives spent below zero."""
        tracker = ClassificationBudgetTracker(budget=0.10)
        await tracker.release(0.05)
        assert tracker.total_spent == 0.0
