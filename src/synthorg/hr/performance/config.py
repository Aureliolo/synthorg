"""Performance tracking configuration."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.time_window import DEFAULT_WINDOW_LABELS
from synthorg.core.types import NotBlankStr
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import MirrorField, apply_settings_mirrors, parse_float


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
            score (default 0.4). The LLM-judge weight is its complement.
        quality_llm_weight: Derived LLM-judge weight
            (``1 - quality_ci_weight``); the composite weights always
            sum to 1.0 by construction.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="llm_sampling_rate",
            namespace=SettingNamespace.HR,
            key="performance_llm_sampling_rate",
            parse=parse_float,
        ),
        MirrorField(
            field="quality_ci_weight",
            namespace=SettingNamespace.HR,
            key="performance_quality_ci_weight",
            parse=parse_float,
        ),
    )

    min_data_points: int = Field(
        default=5,
        ge=1,
        description="Minimum data points for meaningful aggregation",
    )
    windows: tuple[NotBlankStr, ...] = Field(
        default_factory=lambda: tuple(NotBlankStr(w) for w in DEFAULT_WINDOW_LABELS),
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
            "Weight for the CI signal in the composite quality score."
            " The LLM-judge weight is its complement"
            " (1 - quality_ci_weight)."
        ),
    )

    @computed_field
    @property
    def quality_llm_weight(self) -> float:
        """LLM-judge weight, the complement of the CI weight.

        Returns:
            ``1.0 - quality_ci_weight`` so the composite quality weights
            always sum to 1.0 by construction.
        """
        return 1.0 - self.quality_ci_weight

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: object) -> object:
        """Apply settings mirrors.

        Returns:
            Result of type ``object``.
        """
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)

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
