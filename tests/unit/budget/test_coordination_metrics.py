"""Tests for coordination metrics computations."""

import pytest

from synthorg.budget.coordination_metrics import (
    CoordinationEfficiency,
    CoordinationMetrics,
    CoordinationOverhead,
    ErrorAmplification,
    MessageDensity,
    RedundancyRate,
    compute_efficiency,
    compute_error_amplification,
    compute_message_density,
    compute_overhead,
    compute_redundancy_rate,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestComputeEfficiency:
    """compute_efficiency pure function."""

    def test_basic(self) -> None:
        result = compute_efficiency(
            success_rate=0.8,
            turns_mas=10.0,
            turns_sas=5.0,
        )
        assert isinstance(result, CoordinationEfficiency)
        # Ec = 0.8 / (10/5) = 0.8 / 2.0 = 0.4
        assert result.value == pytest.approx(0.4)
        assert result.success_rate == 0.8
        assert result.turns_mas == 10.0
        assert result.turns_sas == 5.0

    def test_equal_turns(self) -> None:
        result = compute_efficiency(
            success_rate=0.9,
            turns_mas=5.0,
            turns_sas=5.0,
        )
        # Ec = 0.9 / (5/5) = 0.9
        assert result.value == pytest.approx(0.9)

    def test_perfect_efficiency(self) -> None:
        result = compute_efficiency(
            success_rate=1.0,
            turns_mas=3.0,
            turns_sas=3.0,
        )
        assert result.value == pytest.approx(1.0)

    def test_zero_success_rate(self) -> None:
        result = compute_efficiency(
            success_rate=0.0,
            turns_mas=10.0,
            turns_sas=5.0,
        )
        assert result.value == 0.0

    def test_zero_turns_sas_raises(self) -> None:
        with pytest.raises(ValueError, match="turns_sas must be positive"):
            compute_efficiency(
                success_rate=0.8,
                turns_mas=10.0,
                turns_sas=0.0,
            )


@pytest.mark.unit
class TestComputeOverhead:
    """compute_overhead pure function."""

    def test_basic(self) -> None:
        result = compute_overhead(turns_mas=10.0, turns_sas=5.0)
        assert isinstance(result, CoordinationOverhead)
        # O% = (10 - 5) / 5 * 100 = 100%
        assert result.value_percent == pytest.approx(100.0)

    def test_no_overhead(self) -> None:
        result = compute_overhead(turns_mas=5.0, turns_sas=5.0)
        assert result.value_percent == pytest.approx(0.0)

    def test_negative_overhead(self) -> None:
        """Multi-agent uses fewer turns than single (unlikely but valid)."""
        result = compute_overhead(turns_mas=3.0, turns_sas=5.0)
        assert result.value_percent == pytest.approx(-40.0)

    def test_zero_turns_sas_raises(self) -> None:
        with pytest.raises(ValueError, match="turns_sas must be positive"):
            compute_overhead(turns_mas=10.0, turns_sas=0.0)


@pytest.mark.unit
class TestComputeErrorAmplification:
    """compute_error_amplification pure function."""

    def test_basic(self) -> None:
        result = compute_error_amplification(
            error_rate_mas=0.2,
            error_rate_sas=0.1,
        )
        assert isinstance(result, ErrorAmplification)
        # Ae = 0.2 / 0.1 = 2.0
        assert result.value == pytest.approx(2.0)

    def test_no_amplification(self) -> None:
        result = compute_error_amplification(
            error_rate_mas=0.1,
            error_rate_sas=0.1,
        )
        assert result.value == pytest.approx(1.0)

    def test_reduction(self) -> None:
        result = compute_error_amplification(
            error_rate_mas=0.05,
            error_rate_sas=0.1,
        )
        assert result.value == pytest.approx(0.5)

    def test_zero_error_rate_sas_raises(self) -> None:
        with pytest.raises(
            ValueError,
            match="error_rate_sas must be positive",
        ):
            compute_error_amplification(
                error_rate_mas=0.2,
                error_rate_sas=0.0,
            )


@pytest.mark.unit
class TestComputeMessageDensity:
    """compute_message_density pure function."""

    def test_basic(self) -> None:
        result = compute_message_density(
            inter_agent_messages=15,
            reasoning_turns=10,
        )
        assert isinstance(result, MessageDensity)
        assert result.value == pytest.approx(1.5)
        assert result.inter_agent_messages == 15
        assert result.reasoning_turns == 10

    def test_zero_messages(self) -> None:
        result = compute_message_density(
            inter_agent_messages=0,
            reasoning_turns=5,
        )
        assert result.value == 0.0

    def test_zero_reasoning_turns_raises(self) -> None:
        with pytest.raises(
            ValueError,
            match="reasoning_turns must be positive",
        ):
            compute_message_density(
                inter_agent_messages=10,
                reasoning_turns=0,
            )


@pytest.mark.unit
class TestComputeRedundancyRate:
    """compute_redundancy_rate pure function."""

    def test_basic(self) -> None:
        result = compute_redundancy_rate(
            similarities=[0.2, 0.4, 0.6],
        )
        assert isinstance(result, RedundancyRate)
        assert result.value == pytest.approx(0.4)
        assert result.sample_count == 3

    def test_all_identical(self) -> None:
        result = compute_redundancy_rate(
            similarities=[1.0, 1.0, 1.0],
        )
        assert result.value == pytest.approx(1.0)

    def test_all_unique(self) -> None:
        result = compute_redundancy_rate(
            similarities=[0.0, 0.0, 0.0],
        )
        assert result.value == pytest.approx(0.0)

    def test_single_value(self) -> None:
        result = compute_redundancy_rate(similarities=[0.5])
        assert result.value == pytest.approx(0.5)
        assert result.sample_count == 1

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            compute_redundancy_rate(similarities=[])

    def test_value_above_one_raises(self) -> None:
        with pytest.raises(ValueError, match="outside"):
            compute_redundancy_rate(similarities=[0.5, 1.1])

    def test_value_below_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="outside"):
            compute_redundancy_rate(similarities=[-0.1, 0.5])


@pytest.mark.unit
class TestCoordinationMetrics:
    """CoordinationMetrics container model."""

    def test_defaults_all_none(self) -> None:
        metrics = CoordinationMetrics()
        assert metrics.efficiency is None
        assert metrics.overhead is None
        assert metrics.error_amplification is None
        assert metrics.message_density is None
        assert metrics.redundancy_rate is None

    def test_with_some_metrics(self) -> None:
        eff = compute_efficiency(
            success_rate=0.9,
            turns_mas=6.0,
            turns_sas=5.0,
        )
        ovh = compute_overhead(turns_mas=6.0, turns_sas=5.0)
        metrics = CoordinationMetrics(efficiency=eff, overhead=ovh)
        assert metrics.efficiency is not None
        assert metrics.overhead is not None
        assert metrics.error_amplification is None

    def test_frozen(self) -> None:
        from pydantic import ValidationError

        metrics = CoordinationMetrics()
        with pytest.raises(ValidationError):
            metrics.efficiency = None  # type: ignore[misc]
