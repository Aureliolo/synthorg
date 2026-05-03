"""Configuration models for procedural memory capture strategies.

Defines the configuration for selecting and tuning capture strategies.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CaptureConfig(BaseModel):
    """Configuration for procedural memory capture strategy selection.

    Specifies which capture strategy to use (failure-only, success-only,
    or hybrid) and quality thresholds for success capture.

    Attributes:
        type: Strategy type: "failure" (failures only), "success"
            (successes only), or "hybrid" (both).
        min_quality_score: Minimum quality score (0-10) for success
            capture. Quality = confidence * 10. Default 8.0 means
            confidence >= 0.8.
        success_quality_percentile: Percentile threshold for success
            quality when using percentile-based filtering. Default 75.0
            means top 25% of successful executions.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    type: Literal["failure", "success", "hybrid"] = Field(
        default="hybrid",
        description='Strategy type: "failure", "success", or "hybrid"',
    )
    min_quality_score: float = Field(
        default=8.0,
        ge=0.0,
        le=10.0,
        description="Minimum quality score (0-10) for success capture",
    )
    success_quality_percentile: float = Field(
        default=75.0,
        ge=0.0,
        le=100.0,
        description="Percentile threshold for success quality filtering",
    )
