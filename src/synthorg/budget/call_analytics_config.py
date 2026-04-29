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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    warn_rate: float = Field(
        default=_DEFAULT_RETRY_WARN_RATE,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of calls with at least one retry that triggers a warning alert."
        ),
    )


class CallAnalyticsConfig(BaseModel):
    """Configuration for the per-call analytics service.

    Controls whether analytics collection and alerting is active and
    what thresholds trigger notification dispatch.

    Attributes:
        enabled: Whether analytics collection and alerting is active.
        orchestration_alerts: Thresholds for orchestration ratio alerting.
        retry_alerts: Configuration for retry rate alerting.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

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
