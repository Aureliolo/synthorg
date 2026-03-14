"""Configuration models for coordination metrics.

Defines config models for controlling which coordination metrics are
collected, error taxonomy, and orchestration alert thresholds.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CoordinationMetricName(StrEnum):
    """Names of individual coordination metrics."""

    EFFICIENCY = "efficiency"
    OVERHEAD = "overhead"
    ERROR_AMPLIFICATION = "error_amplification"
    MESSAGE_DENSITY = "message_density"
    REDUNDANCY = "redundancy"


class ErrorCategory(StrEnum):
    """Error categories for multi-agent error taxonomy."""

    LOGICAL_CONTRADICTION = "logical_contradiction"
    NUMERICAL_DRIFT = "numerical_drift"
    CONTEXT_OMISSION = "context_omission"
    COORDINATION_FAILURE = "coordination_failure"


class ErrorTaxonomyConfig(BaseModel):
    """Configuration for multi-agent error taxonomy tracking.

    Attributes:
        enabled: Whether error taxonomy tracking is enabled.
        categories: Error categories to track (must be unique).
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(
        default=False,
        description="Whether error taxonomy tracking is enabled",
    )
    categories: tuple[ErrorCategory, ...] = Field(
        default=tuple(ErrorCategory),
        description="Error categories to track",
    )

    @model_validator(mode="after")
    def _validate_unique_categories(self) -> Self:
        """Ensure no duplicate categories."""
        if len(self.categories) != len(set(self.categories)):
            msg = "categories must not contain duplicates"
            raise ValueError(msg)
        return self


class OrchestrationAlertThresholds(BaseModel):
    """Thresholds for orchestration overhead alert levels.

    Attributes:
        info: Ratio threshold for INFO alert (default 0.30).
        warn: Ratio threshold for WARNING alert (default 0.50).
        critical: Ratio threshold for CRITICAL alert (default 0.70).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    info: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="Ratio threshold for INFO alert",
    )
    warn: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        description="Ratio threshold for WARNING alert",
    )
    critical: float = Field(
        default=0.70,
        ge=0.0,
        le=1.0,
        description="Ratio threshold for CRITICAL alert",
    )

    @model_validator(mode="after")
    def _validate_threshold_ordering(self) -> Self:
        """Ensure info < warn < critical."""
        if not (self.info < self.warn < self.critical):
            msg = (
                f"Thresholds must be strictly ordered: "
                f"info ({self.info}) < warn ({self.warn}) "
                f"< critical ({self.critical})"
            )
            raise ValueError(msg)
        return self


class CoordinationMetricsConfig(BaseModel):
    """Top-level configuration for coordination metrics collection.

    Attributes:
        enabled: Whether coordination metrics are collected.
        collect: Which metrics to collect.
        baseline_window: Number of recent records for baseline
            computation.
        error_taxonomy: Error taxonomy tracking configuration.
        orchestration_alerts: Orchestration overhead alert thresholds.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(
        default=False,
        description="Whether coordination metrics are collected",
    )
    collect: tuple[CoordinationMetricName, ...] = Field(
        default=tuple(CoordinationMetricName),
        description="Which metrics to collect (must be unique)",
    )
    baseline_window: int = Field(
        default=50,
        gt=0,
        description="Number of recent records for baseline computation",
    )
    error_taxonomy: ErrorTaxonomyConfig = Field(
        default_factory=ErrorTaxonomyConfig,
        description="Error taxonomy tracking configuration",
    )
    orchestration_alerts: OrchestrationAlertThresholds = Field(
        default_factory=OrchestrationAlertThresholds,
        description="Orchestration overhead alert thresholds",
    )

    @model_validator(mode="after")
    def _validate_unique_collect(self) -> Self:
        """Ensure no duplicate metric names in collect."""
        if len(self.collect) != len(set(self.collect)):
            msg = "collect must not contain duplicates"
            raise ValueError(msg)
        return self
