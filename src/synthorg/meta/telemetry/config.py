"""Configuration for cross-deployment analytics.

Defines the frozen config model that controls opt-in telemetry,
collector endpoint settings, and anonymization parameters.
Disabled by default -- requires explicit opt-in.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001


class CrossDeploymentAnalyticsConfig(BaseModel):
    """Configuration for cross-deployment analytics telemetry.

    Safe defaults: everything disabled. Requires explicit opt-in
    with a collector URL and deployment ID salt.

    Attributes:
        enabled: Master switch for cross-deployment analytics.
        collector_url: HTTPS URL to POST anonymized events to.
        collector_enabled: Whether this deployment acts as a
            collector that receives events from other deployments.
            Requires ``enabled=True`` to take effect.
        deployment_id_salt: Secret string hashed to produce a
            non-reversible deployment identifier. Changing it
            breaks deployment correlation in aggregated data.
        industry_tag: Optional industry category for cross-industry
            pattern analysis (user-provided, not inferred).
            Max 100 characters to prevent accidental PII leakage.
        batch_size: Max events to buffer before flushing.
        flush_interval_seconds: Max seconds between flushes.
        http_timeout_seconds: HTTP POST timeout.
        min_deployments_for_pattern: Minimum unique deployments
            required before a pattern is reported.
        recommendation_min_observations: Minimum total events
            required before generating threshold recommendations.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = False
    collector_url: NotBlankStr | None = None
    collector_enabled: bool = False
    deployment_id_salt: NotBlankStr | None = None
    industry_tag: NotBlankStr | None = Field(default=None, max_length=100)
    batch_size: int = Field(default=50, ge=1, le=1000)
    flush_interval_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    http_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    min_deployments_for_pattern: int = Field(default=3, ge=2)
    recommendation_min_observations: int = Field(default=10, ge=3)

    @field_validator("collector_url")
    @classmethod
    def _validate_collector_url_scheme(
        cls,
        v: NotBlankStr | None,
    ) -> NotBlankStr | None:
        """Require HTTPS scheme on collector_url."""
        if v is None:
            return v
        url = str(v)
        if not url.startswith("https://"):
            msg = "collector_url must use HTTPS scheme"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _validate_enabled_requirements(self) -> Self:
        """Require collector_url and salt when analytics is enabled."""
        if not self.enabled:
            return self
        missing: list[str] = []
        if self.collector_url is None:
            missing.append("collector_url")
        if self.deployment_id_salt is None:
            missing.append("deployment_id_salt")
        if missing:
            msg = "cross_deployment_analytics.enabled requires: " + ", ".join(missing)
            raise ValueError(msg)
        return self
