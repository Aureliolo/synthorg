"""Tests for TokenTracker budget tracking."""

import pytest

from ai_company.communication.meeting._token_tracker import TokenTracker

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestTokenTracker:
    """Tests for TokenTracker dataclass."""

    def test_fresh_tracker_state(self) -> None:
        tracker = TokenTracker(budget=1000)
        assert tracker.budget == 1000
        assert tracker.input_tokens == 0
        assert tracker.output_tokens == 0
        assert tracker.used == 0
        assert tracker.remaining == 1000
        assert tracker.is_exhausted is False

    def test_record_tokens(self) -> None:
        tracker = TokenTracker(budget=100)
        tracker.record(10, 20)
        assert tracker.input_tokens == 10
        assert tracker.output_tokens == 20
        assert tracker.used == 30
        assert tracker.remaining == 70

    def test_record_up_to_budget(self) -> None:
        tracker = TokenTracker(budget=100)
        tracker.record(50, 50)
        assert tracker.used == 100
        assert tracker.remaining == 0
        assert tracker.is_exhausted is True

    def test_record_exceeds_budget(self) -> None:
        tracker = TokenTracker(budget=50)
        tracker.record(30, 30)
        assert tracker.used == 60
        assert tracker.remaining == 0
        assert tracker.is_exhausted is True

    def test_remaining_never_negative(self) -> None:
        tracker = TokenTracker(budget=10)
        tracker.record(100, 100)
        assert tracker.remaining == 0
        assert tracker.is_exhausted is True

    def test_is_exhausted_boundary(self) -> None:
        tracker = TokenTracker(budget=30)
        tracker.record(15, 14)
        assert tracker.remaining == 1
        assert tracker.is_exhausted is False
        tracker.record(0, 1)
        assert tracker.remaining == 0
        assert tracker.is_exhausted is True

    def test_multiple_records(self) -> None:
        tracker = TokenTracker(budget=200)
        tracker.record(10, 20)
        tracker.record(30, 40)
        tracker.record(5, 15)
        assert tracker.input_tokens == 45
        assert tracker.output_tokens == 75
        assert tracker.used == 120
        assert tracker.remaining == 80

    def test_negative_input_tokens_rejected(self) -> None:
        tracker = TokenTracker(budget=100)
        with pytest.raises(ValueError, match="non-negative"):
            tracker.record(-1, 10)

    def test_negative_output_tokens_rejected(self) -> None:
        tracker = TokenTracker(budget=100)
        with pytest.raises(ValueError, match="non-negative"):
            tracker.record(10, -5)

    def test_both_negative_rejected(self) -> None:
        tracker = TokenTracker(budget=100)
        with pytest.raises(ValueError, match="non-negative"):
            tracker.record(-1, -1)

    def test_zero_tokens_accepted(self) -> None:
        tracker = TokenTracker(budget=100)
        tracker.record(0, 0)
        assert tracker.used == 0

    @pytest.mark.parametrize("budget", [0, -1, -100])
    def test_non_positive_budget_rejected(self, budget: int) -> None:
        with pytest.raises(ValueError, match="positive"):
            TokenTracker(budget=budget)
