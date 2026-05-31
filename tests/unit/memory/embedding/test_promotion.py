# module-kind: tests
"""Tests for the checkpoint promotion gate.

The gate is a pure, signal-agnostic decision over two measured scores plus a
margin. It is the load-bearing primitive behind "promotes ONLY on a measured
win": the fine-tune orchestrator feeds it the embedding eval A/B (NDCG@10
fine-tuned vs base) and the golden-benchmark sim-harness feeds it two
``Scorecard.total`` values. The single assertion that proves the gate is that a
tie does NOT promote.
"""

import pytest

from synthorg.memory.embedding.promotion import (
    DEFAULT_PROMOTION_MARGIN,
    should_promote,
)

pytestmark = pytest.mark.unit


def test_clear_win_promotes() -> None:
    """A candidate well above base by more than the margin promotes."""
    assert should_promote(0.5, 0.9, margin=0.25) is True


def test_exact_margin_boundary_promotes() -> None:
    """A gain of exactly the margin promotes (binary-exact values)."""
    # 0.5, 0.75, 0.25 are exact binary fractions: 0.75 - 0.5 == 0.25 exactly.
    assert should_promote(0.5, 0.75, margin=0.25) is True


def test_gain_below_margin_does_not_promote() -> None:
    """A positive gain that falls short of the margin is rejected."""
    assert should_promote(0.5, 0.7, margin=0.25) is False


def test_exact_tie_does_not_promote() -> None:
    """A tie is NOT a measured win -- the core "ONLY on a win" assertion."""
    assert should_promote(0.5, 0.5, margin=0.25) is False


def test_tie_does_not_promote_even_at_smallest_margin() -> None:
    """No margin is small enough to let a tie through (margin is strictly > 0)."""
    assert should_promote(0.5, 0.5, margin=DEFAULT_PROMOTION_MARGIN) is False


def test_loss_does_not_promote() -> None:
    """A regression (candidate below base) is rejected."""
    assert should_promote(0.6, 0.4, margin=DEFAULT_PROMOTION_MARGIN) is False


def test_default_margin_is_a_positive_gain() -> None:
    """The default margin is strictly positive, so it cannot pass a tie."""
    assert DEFAULT_PROMOTION_MARGIN > 0.0


def test_win_under_default_margin_promotes() -> None:
    """The orchestrator's NDCG A/B (0.6 vs 0.5) clears the default margin."""
    assert should_promote(0.5, 0.6) is True


@pytest.mark.parametrize("bad_margin", [0.0, -0.01, -1.0])
def test_non_positive_margin_is_rejected(bad_margin: float) -> None:
    """A margin <= 0 would let a tie promote, so the gate refuses it."""
    with pytest.raises(ValueError, match="margin must be positive"):
        should_promote(0.5, 0.6, margin=bad_margin)


def test_signal_agnostic_over_benchmark_totals() -> None:
    """The same gate decides over coarse Scorecard totals, not just NDCG."""
    assert should_promote(72.0, 80.0, margin=5.0) is True
    assert should_promote(72.0, 74.0, margin=5.0) is False
    assert should_promote(72.0, 72.0, margin=5.0) is False
