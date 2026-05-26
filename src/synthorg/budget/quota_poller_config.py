"""Configuration for the proactive quota polling service."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class QuotaAlertThresholds(BaseModel):
    """Usage percentage thresholds for quota alerts.

    Attributes:
        warn_pct: Usage percentage that triggers a WARNING alert.
            Must be in [0.0, 100.0] and strictly less than
            ``critical_pct``.
        critical_pct: Usage percentage that triggers a CRITICAL alert.
            Must be in [0.0, 100.0] and strictly greater than
            ``warn_pct``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    warn_pct: float = Field(
        default=80.0,
        ge=0.0,
        le=100.0,
        description="Usage percentage that triggers a WARNING alert.",
    )
    critical_pct: float = Field(
        default=95.0,
        ge=0.0,
        le=100.0,
        description="Usage percentage that triggers a CRITICAL alert.",
    )

    @model_validator(mode="after")
    def _validate_ordering(self) -> Self:
        """Ensure warn_pct < critical_pct.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.warn_pct >= self.critical_pct:
            msg = (
                f"warn_pct ({self.warn_pct}) must be strictly less than "
                f"critical_pct ({self.critical_pct})"
            )
            raise ValueError(msg)
        return self


class QuotaPollerConfig(BaseModel):
    """Configuration for the proactive quota polling service.

    Attributes:
        enabled: Whether the poller is active.  Defaults to ``False``
            so it must be explicitly opted in.
        poll_interval_seconds: How often to poll quota snapshots.
            Must be in (0, 3600].
        alert_thresholds: Usage percentage thresholds for alerts.
        cooldown_seconds: Minimum seconds between repeated alerts for
            the same provider/window/level tuple.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether quota polling is active.",
    )
    poll_interval_seconds: float = Field(
        default=60.0,
        gt=0.0,
        le=3600.0,
        description="How often to poll quota snapshots, in seconds.",
    )
    alert_thresholds: QuotaAlertThresholds = Field(
        default_factory=QuotaAlertThresholds,
        description="Usage percentage thresholds for dispatching alerts.",
    )
    cooldown_seconds: float = Field(
        default=300.0,
        ge=0.0,
        description=(
            "Minimum seconds between repeated alerts for the same "
            "provider/window/level tuple."
        ),
    )
