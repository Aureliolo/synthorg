"""Configuration for the per-call analytics service."""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.budget.coordination_config import OrchestrationAlertThresholds

# 10% of calls with at least one retry is the operator-default warning
# threshold. Tuned against historical incident data; tighten via
# ``RetryAlertConfig(warn_rate=...)`` overrides per deployment.
_DEFAULT_RETRY_WARN_RATE: Final[float] = 0.10


class RetryAlertConfig(BaseModel):
    """Configuration for retry rate alerting.

    Attributes:
        warn_rate: Fraction of calls with retries that triggers a warning
            alert.  Must be in [0.0, 1.0].
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    warn_rate: float = Field(
        default=_DEFAULT_RETRY_WARN_RATE,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of calls with at least one retry that triggers a warning alert."
        ),
    )


class PromptClassAlertConfig(BaseModel):
    """Per-prompt-purpose cost / latency alert thresholds.

    Both thresholds are opt-in (``None`` disables that dimension) so a
    deployment alerts only on the bounds it cares about; the cost ceiling is
    currency-specific and the latency ceiling deployment-specific, so neither
    carries a privileged default.

    Attributes:
        cost_warn: A purpose whose total cost over the window exceeds this
            triggers a warning. ``None`` disables cost alerting.
        p95_latency_warn_ms: A purpose whose p95 latency exceeds this (in
            milliseconds) triggers a warning. ``None`` disables latency
            alerting.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    cost_warn: float | None = Field(
        default=None,
        ge=0.0,
        description="Per-purpose total-cost warning ceiling, or None to disable.",
    )
    p95_latency_warn_ms: float | None = Field(
        default=None,
        ge=0.0,
        description="Per-purpose p95-latency warning ceiling in ms, or None.",
    )


class CallAnalyticsConfig(BaseModel):
    """Configuration for the per-call analytics service.

    Controls whether analytics collection and alerting is active and
    what thresholds trigger notification dispatch.

    Attributes:
        enabled: Whether analytics collection and alerting is active.
        orchestration_alerts: Thresholds for orchestration ratio alerting.
        retry_alerts: Configuration for retry rate alerting.
        prompt_class_alerts: Per-prompt-purpose cost / latency thresholds.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Whether analytics collection and alerting is active.",
    )
    orchestration_alerts: OrchestrationAlertThresholds = Field(
        default_factory=OrchestrationAlertThresholds,
        description="Thresholds for orchestration ratio alerting.",
    )
    retry_alerts: RetryAlertConfig = Field(
        default_factory=RetryAlertConfig,
        description="Configuration for retry rate alerting.",
    )
    prompt_class_alerts: PromptClassAlertConfig = Field(
        default_factory=PromptClassAlertConfig,
        description="Per-prompt-purpose cost / latency alert thresholds.",
    )
