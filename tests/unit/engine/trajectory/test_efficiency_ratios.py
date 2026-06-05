"""Tests for efficiency ratio models and computation."""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from synthorg.execution.efficiency import (
    EfficiencyRatios,
    IdealTrajectoryBaseline,
    compute_efficiency_ratios,
)


def _make_baseline(**overrides: Any) -> IdealTrajectoryBaseline:
    defaults: dict[str, Any] = {
        "task_type": "test-task",
        "ideal_step_count": 10,
        "ideal_tool_call_count": 5,
        "ideal_latency_seconds": 30.0,
        "ideal_output_tokens": 500,
        "ideal_structural_score": 0.1,
        "ideal_pte": 1000.0,
        "recorded_at": datetime(2026, 1, 1, tzinfo=UTC),
        "recorded_by_agent_id": "test-agent",
        "model_tier": "medium",
    }
    defaults.update(overrides)
    return IdealTrajectoryBaseline(**defaults)


@pytest.mark.unit
class TestIdealTrajectoryBaseline:
    """IdealTrajectoryBaseline frozen model validation."""

    def test_valid_construction(self) -> None:
        baseline = _make_baseline()
        assert baseline.task_type == "test-task"
        assert baseline.ideal_step_count == 10
        assert baseline.model_tier == "medium"
        assert baseline.notes == ""

    def test_frozen(self) -> None:
        baseline = _make_baseline()
        with pytest.raises(ValidationError):
            baseline.task_type = "changed"  # type: ignore[misc]

    def test_step_count_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            _make_baseline(ideal_step_count=0)

    def test_latency_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            _make_baseline(ideal_latency_seconds=0.0)

    def test_pte_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            _make_baseline(ideal_pte=0.0)

    def test_structural_score_bounds(self) -> None:
        _make_baseline(ideal_structural_score=0.0)
        _make_baseline(ideal_structural_score=1.0)
        with pytest.raises(ValidationError):
            _make_baseline(ideal_structural_score=1.1)

    def test_model_tier_values(self) -> None:
        for tier in ("small", "medium", "large"):
            baseline = _make_baseline(model_tier=tier)
            assert baseline.model_tier == tier


@pytest.mark.unit
class TestEfficiencyRatios:
    """EfficiencyRatios frozen model validation."""

    def _make_ratios(self, **overrides: Any) -> EfficiencyRatios:
        defaults: dict[str, Any] = {
            "step_ratio": 1.0,
            "tool_call_ratio": 1.0,
            "latency_ratio": 1.0,
            "verbosity_ratio": 1.0,
            "structural_erosion_score": 0.0,
            "pte": 1000.0,
            "pte_ratio": 1.0,
            "baseline_version": "test-task:20260101",
        }
        defaults.update(overrides)
        return EfficiencyRatios(**defaults)

    def test_valid_construction(self) -> None:
        ratios = self._make_ratios()
        assert ratios.step_ratio == 1.0
        assert ratios.verbosity_delta_per_iteration == ()

    def test_frozen(self) -> None:
        ratios = self._make_ratios()
        with pytest.raises(ValidationError):
            ratios.step_ratio = 2.0  # type: ignore[misc]

    def test_erosion_score_bounds(self) -> None:
        self._make_ratios(structural_erosion_score=0.0)
        self._make_ratios(structural_erosion_score=1.0)
        with pytest.raises(ValidationError):
            self._make_ratios(structural_erosion_score=1.1)

    def test_erosion_score_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._make_ratios(structural_erosion_score=-0.1)

    def test_verbosity_deltas(self) -> None:
        ratios = self._make_ratios(
            verbosity_delta_per_iteration=(0.1, 0.2, 0.3),
        )
        assert ratios.verbosity_delta_per_iteration == (0.1, 0.2, 0.3)


@pytest.mark.unit
class TestComputeEfficiencyRatios:
    """compute_efficiency_ratios function."""

    def test_on_target(self) -> None:
        baseline = _make_baseline()
        ratios = compute_efficiency_ratios(
            baseline=baseline,
            observed_steps=10,
            observed_tool_calls=5,
            observed_latency_seconds=30.0,
            observed_output_tokens=500,
            structural_erosion_score=0.0,
            verbosity_deltas=(),
            observed_pte=1000.0,
        )
        assert ratios.step_ratio == 1.0
        assert ratios.tool_call_ratio == 1.0
        assert ratios.latency_ratio == 1.0
        assert ratios.verbosity_ratio == 1.0
        assert ratios.pte_ratio == 1.0

    def test_worse_than_baseline(self) -> None:
        baseline = _make_baseline()
        ratios = compute_efficiency_ratios(
            baseline=baseline,
            observed_steps=20,
            observed_tool_calls=10,
            observed_latency_seconds=60.0,
            observed_output_tokens=1000,
            structural_erosion_score=0.5,
            verbosity_deltas=(0.2, 0.4),
            observed_pte=2000.0,
        )
        assert ratios.step_ratio == 2.0
        assert ratios.tool_call_ratio == 2.0
        assert ratios.latency_ratio == 2.0
        assert ratios.verbosity_ratio == 2.0
        assert ratios.pte_ratio == 2.0

    def test_zero_ideal_tool_calls_with_observed(self) -> None:
        """When baseline expects zero tool calls but agent used tools,
        ratio equals the raw observed count to signal unexpected calls."""
        baseline = _make_baseline(ideal_tool_call_count=0)
        ratios = compute_efficiency_ratios(
            baseline=baseline,
            observed_steps=10,
            observed_tool_calls=3,
            observed_latency_seconds=30.0,
            observed_output_tokens=500,
            structural_erosion_score=0.0,
            verbosity_deltas=(),
            observed_pte=1000.0,
        )
        assert ratios.tool_call_ratio == 3.0

    def test_zero_ideal_and_zero_observed_tool_calls(self) -> None:
        """When both baseline and observed are zero, ratio is 0.0."""
        baseline = _make_baseline(ideal_tool_call_count=0)
        ratios = compute_efficiency_ratios(
            baseline=baseline,
            observed_steps=10,
            observed_tool_calls=0,
            observed_latency_seconds=30.0,
            observed_output_tokens=500,
            structural_erosion_score=0.0,
            verbosity_deltas=(),
            observed_pte=1000.0,
        )
        assert ratios.tool_call_ratio == 0.0
