"""Pydantic models for the nine coordination metrics.

Metric shapes defined in the Operations design page (Coordination
Metrics): efficiency, overhead, error amplification, message density,
redundancy rate, Amdahl ceiling, straggler gap, token/speedup ratio, and
message overhead, plus the :class:`CoordinationMetrics` container. Pure
computation lives in :mod:`synthorg.budget.coordination_metrics`.
"""

import math
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.coordination_metrics import (
    COORD_METRICS_VALIDATION_ERROR,
)

logger = get_logger(__name__)

# Amdahl "90% of max speedup" coefficient: S(n) >= 0.9 * S_max solves to
# n >= 9p/(1-p), so the coefficient is 9.
_AMDAHL_90PCT_COEFFICIENT: Final[float] = 9.0


class CoordinationEfficiency(BaseModel):
    """Coordination efficiency: success rate adjusted for turn overhead.

    ``Ec = success_rate / (turns_mas / turns_sas)``

    Attributes:
        value: Computed efficiency (higher is better).
        success_rate: Multi-agent task success rate.
        turns_mas: Average turns for multi-agent tasks.
        turns_sas: Average turns for single-agent tasks.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    success_rate: float = Field(
        ge=0.0,
        le=1.0,
        description="Multi-agent success rate",
    )
    turns_mas: float = Field(gt=0, description="Avg turns (multi-agent)")
    turns_sas: float = Field(gt=0, description="Avg turns (single-agent)")

    @computed_field(  # type: ignore[prop-decorator]
        description="Coordination efficiency",
    )
    @property
    def value(self) -> float:
        """Computed efficiency: ``success_rate / (turns_mas / turns_sas)``."""
        return self.success_rate / (self.turns_mas / self.turns_sas)


class CoordinationOverhead(BaseModel):
    """Coordination overhead: percentage of extra turns for multi-agent.

    ``O% = (turns_mas - turns_sas) / turns_sas * 100``

    Attributes:
        value_percent: Overhead percentage.
        turns_mas: Average turns for multi-agent tasks.
        turns_sas: Average turns for single-agent tasks.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    turns_mas: float = Field(gt=0, description="Avg turns (multi-agent)")
    turns_sas: float = Field(gt=0, description="Avg turns (single-agent)")

    @computed_field(  # type: ignore[prop-decorator]
        description="Overhead percentage",
    )
    @property
    def value_percent(self) -> float:
        """Overhead: ``(turns_mas - turns_sas) / turns_sas * 100``."""
        return (self.turns_mas - self.turns_sas) / self.turns_sas * 100


class ErrorAmplification(BaseModel):
    """Error amplification: ratio of multi-agent to single-agent error rates.

    ``Ae = error_rate_mas / error_rate_sas``

    Attributes:
        value: Amplification factor (>1 means more errors in MAS).
        error_rate_mas: Multi-agent error rate.
        error_rate_sas: Single-agent error rate.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    error_rate_mas: float = Field(
        ge=0.0,
        description="Multi-agent error rate",
    )
    error_rate_sas: float = Field(gt=0, description="Single-agent error rate")

    @computed_field(  # type: ignore[prop-decorator]
        description="Error amplification factor",
    )
    @property
    def value(self) -> float:
        """Amplification: ``error_rate_mas / error_rate_sas``."""
        return self.error_rate_mas / self.error_rate_sas


class MessageDensity(BaseModel):
    """Message density: inter-agent messages per reasoning turn.

    ``c = inter_agent_messages / reasoning_turns``

    Attributes:
        value: Messages per turn.
        inter_agent_messages: Number of inter-agent messages.
        reasoning_turns: Number of reasoning turns.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    inter_agent_messages: int = Field(
        ge=0,
        description="Inter-agent message count",
    )
    reasoning_turns: int = Field(
        gt=0,
        description="Reasoning turn count",
    )

    @computed_field(  # type: ignore[prop-decorator]
        description="Messages per reasoning turn",
    )
    @property
    def value(self) -> float:
        """Density: ``inter_agent_messages / reasoning_turns``."""
        return self.inter_agent_messages / self.reasoning_turns


class RedundancyRate(BaseModel):
    """Redundancy rate: mean similarity across output pairs.

    ``R = mean(similarities)``

    Attributes:
        value: Mean redundancy (0.0-1.0).
        sample_count: Number of similarity samples.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    value: float = Field(
        ge=0.0,
        le=1.0,
        description="Mean redundancy",
    )
    sample_count: int = Field(
        ge=0,
        description="Number of similarity samples",
    )


class AmdahlCeiling(BaseModel):
    """Amdahl's Law speedup ceiling for team sizing.

    ``S_max = 1 / (1 - p)`` where ``p`` is the parallelizable
    fraction of the workload.

    Attributes:
        parallelizable_fraction: Fraction of workload that can be
            parallelized (0.0--1.0, exclusive of 1.0).
        max_speedup: Theoretical maximum speedup (computed).
        recommended_team_size: Team size at 90% of max speedup.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    parallelizable_fraction: float = Field(
        ge=0.0,
        lt=1.0,
        description="Parallelizable workload fraction (0.0--<1.0)",
    )

    @computed_field(  # type: ignore[prop-decorator]
        description="Maximum theoretical speedup",
    )
    @property
    def max_speedup(self) -> float:
        """Amdahl ceiling: ``1 / (1 - p)``."""
        return 1.0 / (1.0 - self.parallelizable_fraction)

    @computed_field(  # type: ignore[prop-decorator]
        description="Team size at 90% of max speedup",
    )
    @property
    def recommended_team_size(self) -> int:
        """Team size where speedup reaches 90% of the ceiling.

        Derived from ``S(n) = 1 / ((1-p) + p/n) >= 0.9 * S_max``.
        Solves to ``n >= 9 * p / (1-p)`` (rounded up, minimum 1).
        """
        p = self.parallelizable_fraction
        if p <= 0:
            return 1
        n = _AMDAHL_90PCT_COEFFICIENT * p / (1.0 - p)
        return max(1, math.ceil(n))


class StragglerGap(BaseModel):
    """Straggler gap: slowest agent vs mean completion time.

    Diagnostic for decentralized topology inefficiency.

    Attributes:
        slowest_duration_seconds: Duration of the slowest agent.
        mean_duration_seconds: Mean duration across all agents.
        gap_seconds: Absolute gap (slowest - mean, computed).
        gap_ratio: Relative gap (gap / mean, computed).
        slowest_agent_id: Identifier of the slowest agent.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    slowest_duration_seconds: float = Field(
        ge=0.0,
        description="Slowest agent duration (seconds)",
    )
    mean_duration_seconds: float = Field(
        gt=0.0,
        description="Mean agent duration (seconds)",
    )
    slowest_agent_id: NotBlankStr = Field(
        description="Identifier of the slowest agent",
    )

    @model_validator(mode="after")
    def _validate_slowest_ge_mean(self) -> Self:
        """Validate slowest ge mean.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.slowest_duration_seconds < self.mean_duration_seconds:
            msg = (
                f"slowest_duration_seconds ({self.slowest_duration_seconds}) "
                f"must be >= mean_duration_seconds "
                f"({self.mean_duration_seconds})"
            )
            logger.warning(
                COORD_METRICS_VALIDATION_ERROR,
                slowest=self.slowest_duration_seconds,
                mean=self.mean_duration_seconds,
                error=msg,
            )
            raise ValueError(msg)
        return self

    @computed_field(  # type: ignore[prop-decorator]
        description="Absolute gap (slowest - mean)",
    )
    @property
    def gap_seconds(self) -> float:
        """Absolute gap: slowest - mean."""
        return self.slowest_duration_seconds - self.mean_duration_seconds

    @computed_field(  # type: ignore[prop-decorator]
        description="Relative gap (gap / mean)",
    )
    @property
    def gap_ratio(self) -> float:
        """Relative gap: gap / mean."""
        return self.gap_seconds / self.mean_duration_seconds


# Alerts trigger when the token-to-speedup ratio exceeds this value:
# paying twice as many tokens for the same wall-clock win is the
# empirical "diminishing returns" point operators care about,
# regardless of agent count.
#
# Module-level (not class-level) so the ``alert`` computed_field does
# not pay a Pydantic field-table lookup per instance -- a class-level
# float annotation is otherwise picked up by Pydantic's model-fields
# inspection and adds measurable overhead (~26μs per call observed on
# the CodSpeed benchmark).
_DEFAULT_TOKEN_SPEEDUP_ALERT_RATIO: Final[float] = 2.0


class TokenSpeedupRatio(BaseModel):
    """Token cost vs latency speedup ratio.

    Alerts when tokens scale faster than speedup -- the threshold lives
    in :data:`_DEFAULT_TOKEN_SPEEDUP_ALERT_RATIO` so it stays uniform
    across this module and the alert helper.

    Attributes:
        token_multiplier: ``tokens_mas / tokens_sas``.
        latency_speedup: ``duration_sas / duration_mas``.
        ratio: ``token_multiplier / latency_speedup`` (computed).
        alert: Whether ratio exceeds the alert threshold (computed).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    token_multiplier: float = Field(
        gt=0.0,
        description="Token cost multiplier (MAS / SAS)",
    )
    latency_speedup: float = Field(
        gt=0.0,
        description="Latency speedup (SAS / MAS)",
    )

    @computed_field(  # type: ignore[prop-decorator]
        description="Token/speedup ratio",
    )
    @property
    def ratio(self) -> float:
        """Token multiplier divided by latency speedup."""
        return self.token_multiplier / self.latency_speedup

    @computed_field(  # type: ignore[prop-decorator]
        description="Alert when ratio exceeds the alert threshold",
    )
    @property
    def alert(self) -> bool:
        """True when paying disproportionately more tokens than speed gained."""
        return self.ratio > _DEFAULT_TOKEN_SPEEDUP_ALERT_RATIO


class MessageOverhead(BaseModel):
    """O(n^2) message overhead detection.

    Flags when inter-agent message count suggests quadratic
    coordination overhead.

    Attributes:
        team_size: Number of agents in the coordination.
        message_count: Actual inter-agent message count.
        quadratic_threshold: Fraction of n^2 that triggers alert.
        is_quadratic: Whether message count exceeds threshold.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    team_size: int = Field(
        gt=0,
        description="Number of coordinating agents",
    )
    message_count: int = Field(
        ge=0,
        description="Actual inter-agent message count",
    )
    quadratic_threshold: float = Field(
        default=0.5,
        gt=0.0,
        le=1.0,
        description="Fraction of n^2 that triggers alert",
    )

    @computed_field(  # type: ignore[prop-decorator]
        description="Whether message growth is quadratic",
    )
    @property
    def is_quadratic(self) -> bool:
        """True when messages exceed team_size^2 * threshold."""
        return self.message_count > (self.team_size**2 * self.quadratic_threshold)


class CoordinationMetrics(BaseModel):
    """Container for all nine coordination metrics.

    All fields are optional (``None`` when not collected).

    Attributes:
        efficiency: Coordination efficiency metric.
        overhead: Coordination overhead metric.
        error_amplification: Error amplification metric.
        message_density: Message density metric.
        redundancy_rate: Redundancy rate metric.
        amdahl_ceiling: Amdahl's Law speedup ceiling.
        straggler_gap: Slowest-agent gap metric.
        token_speedup_ratio: Token cost vs speedup ratio.
        message_overhead: O(n^2) message overhead detection.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    efficiency: CoordinationEfficiency | None = Field(
        default=None,
        description="Coordination efficiency",
    )
    overhead: CoordinationOverhead | None = Field(
        default=None,
        description="Coordination overhead",
    )
    error_amplification: ErrorAmplification | None = Field(
        default=None,
        description="Error amplification",
    )
    message_density: MessageDensity | None = Field(
        default=None,
        description="Message density",
    )
    redundancy_rate: RedundancyRate | None = Field(
        default=None,
        description="Redundancy rate",
    )
    amdahl_ceiling: AmdahlCeiling | None = Field(
        default=None,
        description="Amdahl's Law speedup ceiling",
    )
    straggler_gap: StragglerGap | None = Field(
        default=None,
        description="Slowest-agent gap metric",
    )
    token_speedup_ratio: TokenSpeedupRatio | None = Field(
        default=None,
        description="Token cost vs speedup ratio",
    )
    message_overhead: MessageOverhead | None = Field(
        default=None,
        description="O(n^2) message overhead detection",
    )
