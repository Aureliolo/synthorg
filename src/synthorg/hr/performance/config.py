"""Performance tracking configuration."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr


class PerformanceConfig(BaseModel):
    """Configuration for the performance tracking system.

    Attributes:
        min_data_points: Minimum data points for meaningful aggregation.
        windows: Time window labels for rolling metrics.
        improving_threshold: Slope threshold for improving trend.
        declining_threshold: Slope threshold for declining trend.
        collaboration_weights: Optional custom weights for collaboration
            scoring components.
        llm_sampling_rate: Fraction of collaboration events sampled by
            LLM (0.01 = 1%).
        llm_sampling_model: Model ID for LLM calibration sampling
            (None = disabled).
        calibration_retention_days: Days to retain LLM calibration
            records.
        quality_judge_model: Model ID for LLM quality judge
            (None = disabled).
        quality_judge_provider: Provider name for LLM quality judge
            (None = auto from model ref). Requires quality_judge_model.
        quality_ci_weight: Weight for CI signal in composite quality
            score (default 0.4).
        quality_llm_weight: Weight for LLM judge in composite quality
            score (default 0.6).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    min_data_points: int = Field(
        default=5,
        ge=1,
        description="Minimum data points for meaningful aggregation",
    )
    windows: tuple[NotBlankStr, ...] = Field(
        default=(
            NotBlankStr("7d"),
            NotBlankStr("30d"),
            NotBlankStr("90d"),
        ),
        min_length=1,
        description="Time window labels for rolling metrics",
    )
    improving_threshold: float = Field(
        default=0.05,
        description="Slope threshold for improving trend",
    )
    declining_threshold: float = Field(
        default=-0.05,
        description="Slope threshold for declining trend",
    )
    collaboration_weights: Mapping[str, float] | None = Field(
        default=None,
        description="Custom weights for collaboration scoring components",
    )
    llm_sampling_rate: float = Field(
        default=0.01,
        ge=0.0,
        le=1.0,
        description="Fraction of collaboration events sampled by LLM (0.01 = 1%)",
    )
    llm_sampling_model: NotBlankStr | None = Field(
        default=None,
        description="Model ID for LLM calibration sampling (None = disabled)",
    )
    calibration_retention_days: int = Field(
        default=90,
        ge=1,
        description="Days to retain LLM calibration records",
    )
    quality_judge_model: NotBlankStr | None = Field(
        default=None,
        description="Model ID for LLM quality judge (None = disabled)",
    )
    quality_judge_provider: NotBlankStr | None = Field(
        default=None,
        description="Provider name for LLM quality judge (None = auto from model ref)",
    )
    quality_ci_weight: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description=(
            "Weight for CI signal in composite quality score. "
            "Together with quality_llm_weight, must sum to 1.0."
        ),
    )
    quality_llm_weight: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description=(
            "Weight for LLM judge in composite quality score. "
            "Together with quality_ci_weight, must sum to 1.0."
        ),
    )

    _WEIGHT_TOLERANCE: ClassVar[float] = 1e-6

    @model_validator(mode="after")
    def _validate_quality_judge_provider_requires_model(self) -> Self:
        """Ensure quality_judge_provider is not set without a model.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.quality_judge_provider is not None and self.quality_judge_model is None:
            msg = "quality_judge_provider requires quality_judge_model to be set"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_threshold_ordering(self) -> Self:
        """Ensure improving_threshold > declining_threshold.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.improving_threshold <= self.declining_threshold:
            msg = (
                f"improving_threshold ({self.improving_threshold}) must be "
                f"> declining_threshold ({self.declining_threshold})"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_quality_weights_sum(self) -> Self:
        """Ensure quality weights sum to 1.0 (within tolerance).

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        total = self.quality_ci_weight + self.quality_llm_weight
        if abs(total - 1.0) > self._WEIGHT_TOLERANCE:
            msg = (
                f"quality_ci_weight ({self.quality_ci_weight}) + "
                f"quality_llm_weight ({self.quality_llm_weight}) = "
                f"{total}, must sum to 1.0"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _freeze_collaboration_weights(self) -> Self:
        """Wrap ``collaboration_weights`` in a read-only proxy at construction.

        Returns:
            The instance with the weights mapping replaced by a
            ``MappingProxyType`` (no-op when ``None``).
        """
        if self.collaboration_weights is not None:
            object.__setattr__(
                self,
                "collaboration_weights",
                MappingProxyType(dict(self.collaboration_weights)),
            )
        return self
