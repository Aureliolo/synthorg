"""Tests for PerformanceConfig quality weight validation."""

import pytest
from pydantic import ValidationError

from synthorg.hr.performance.config import PerformanceConfig


@pytest.mark.unit
class TestQualityWeightDerivation:
    """quality_llm_weight is the derived complement of quality_ci_weight."""

    @pytest.mark.parametrize(
        ("ci_w", "expected_llm_w"),
        [
            (0.4, 0.6),
            (0.0, 1.0),
            (1.0, 0.0),
            (0.5, 0.5),
            (0.3, 0.7),
        ],
    )
    def test_llm_weight_is_complement(
        self,
        ci_w: float,
        expected_llm_w: float,
    ) -> None:
        """The LLM weight always completes the CI weight to 1.0."""
        cfg = PerformanceConfig(quality_ci_weight=ci_w)
        assert cfg.quality_ci_weight == ci_w
        assert cfg.quality_llm_weight == pytest.approx(expected_llm_w)

    @pytest.mark.parametrize("ci_w", [-0.1, 1.1])
    def test_ci_weight_out_of_range_rejected(self, ci_w: float) -> None:
        """A CI weight outside [0.0, 1.0] raises ValidationError."""
        with pytest.raises(ValidationError):
            PerformanceConfig(quality_ci_weight=ci_w)

    def test_llm_weight_not_settable(self) -> None:
        """quality_llm_weight is computed and rejected as a constructor arg."""
        with pytest.raises(ValidationError, match="quality_llm_weight"):
            PerformanceConfig(quality_llm_weight=0.7)  # type: ignore[call-arg]

    def test_default_weights(self) -> None:
        """Default config derives 0.6 from the 0.4 CI default."""
        cfg = PerformanceConfig()
        assert cfg.quality_ci_weight == 0.4
        assert cfg.quality_llm_weight == pytest.approx(0.6)


@pytest.mark.unit
class TestProviderRequiresModelValidation:
    """quality_judge_provider requires quality_judge_model."""

    def test_provider_without_model_raises(self) -> None:
        """Setting provider without model raises ValidationError."""
        with pytest.raises(ValidationError, match="quality_judge_provider requires"):
            PerformanceConfig(
                quality_judge_provider="test-provider",
            )

    def test_provider_with_model_valid(self) -> None:
        """Setting both provider and model is accepted."""
        cfg = PerformanceConfig(
            quality_judge_model="test-basic-001",
            quality_judge_provider="test-provider",
        )
        assert cfg.quality_judge_model == "test-basic-001"
        assert cfg.quality_judge_provider == "test-provider"

    def test_model_without_provider_valid(self) -> None:
        """Setting model without provider is accepted (auto-resolve)."""
        cfg = PerformanceConfig(
            quality_judge_model="test-basic-001",
        )
        assert cfg.quality_judge_model == "test-basic-001"
        assert cfg.quality_judge_provider is None
