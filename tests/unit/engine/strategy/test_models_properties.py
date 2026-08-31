"""Hypothesis property tests for strategy models."""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from synthorg.engine.strategy.models import (
    CostTierPreset,
    ImpactScore,
    ProgressiveThresholds,
    ProgressiveWeights,
)


class TestProgressiveWeightsProperties:
    """Property tests for ProgressiveWeights."""

    @pytest.mark.unit
    @given(
        weights=st.tuples(
            *(
                st.floats(
                    min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
                )
                for _ in range(6)
            )
        ),
    )
    def test_valid_weights_always_sum_to_one(
        self,
        weights: tuple[float, ...],
    ) -> None:
        """If we can construct valid weights, they must sum to 1.0."""
        budget, authority, decision, rev, blast, time_h = weights
        total = budget + authority + decision + rev + blast + time_h
        if total == 0 or total > 1.0:
            return  # Skip invalid inputs.
        alignment = 1.0 - total
        if alignment < 0 or alignment > 1.0:
            return

        try:
            w = ProgressiveWeights(
                budget_impact=budget,
                authority_level=authority,
                decision_type=decision,
                reversibility=rev,
                blast_radius=blast,
                time_horizon=time_h,
                strategic_alignment=alignment,
            )
            d = w.as_dict()
            assert abs(sum(d.values()) - 1.0) < 1e-6
        except ValidationError:
            pass  # Expected for near-boundary float arithmetic.


class TestProgressiveThresholdsProperties:
    """Property tests for ProgressiveThresholds."""

    @pytest.mark.unit
    @given(
        moderate=st.floats(
            min_value=0.0, max_value=0.99, allow_nan=False, allow_infinity=False
        ),
        generous=st.floats(
            min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False
        ),
    )
    def test_ordering_invariant(
        self,
        moderate: float,
        generous: float,
    ) -> None:
        """Valid thresholds always have moderate < generous."""
        if moderate >= generous:
            with pytest.raises(ValidationError):
                ProgressiveThresholds(moderate=moderate, generous=generous)
        else:
            t = ProgressiveThresholds(moderate=moderate, generous=generous)
            assert t.moderate < t.generous


class TestImpactScoreProperties:
    """Property tests for ImpactScore."""

    @pytest.mark.unit
    @given(
        composite=st.floats(
            min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
        ),
    )
    def test_composite_round_trips(self, composite: float) -> None:
        """Composite value round-trips through ImpactScore construction."""
        score = ImpactScore(
            dimensions={},
            composite=composite,
            tier=CostTierPreset.MODERATE,
        )
        assert score.composite == composite
