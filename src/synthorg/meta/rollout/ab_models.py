"""Data models for the A/B test rollout strategy.

Defines group assignment, per-group metrics, and comparison
result models used by ``ABTestRollout`` and ``ABTestComparator``.
"""

import math
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Self
from uuid import UUID  # noqa: TC003 -- Pydantic needs at runtime

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic needs at runtime


class ABTestGroup(StrEnum):
    """Which group an agent belongs to in an A/B test."""

    CONTROL = "control"
    TREATMENT = "treatment"


class ABTestVerdict(StrEnum):
    """Outcome of comparing control vs treatment groups."""

    TREATMENT_WINS = "treatment_wins"
    CONTROL_WINS = "control_wins"
    INCONCLUSIVE = "inconclusive"
    TREATMENT_REGRESSED = "treatment_regressed"


class GroupAssignment(BaseModel):
    """Deterministic assignment of agents to control/treatment groups.

    Attributes:
        proposal_id: Which proposal this assignment belongs to.
        control_agent_ids: Agent IDs in the control group.
        treatment_agent_ids: Agent IDs in the treatment group.
        control_fraction: Fraction used for the control group.
        assigned_at: When the assignment was computed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    proposal_id: UUID
    control_agent_ids: tuple[NotBlankStr, ...] = ()
    treatment_agent_ids: tuple[NotBlankStr, ...] = ()
    control_fraction: float = Field(gt=0.0, lt=1.0)
    assigned_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )

    @model_validator(mode="after")
    def _validate_disjoint_groups(self) -> Self:
        """Control and treatment groups must not overlap."""
        overlap = set(self.control_agent_ids) & set(
            self.treatment_agent_ids,
        )
        if overlap:
            msg = "control and treatment groups must be disjoint"
            raise ValueError(msg)
        return self


_MAX_QUALITY: Final[float] = 10.0


class GroupMetrics(BaseModel):
    """Sample-backed aggregated metrics for a single A/B test group.

    Raw per-agent observation tuples drive the comparator. The
    ``avg_*`` and ``total_spend`` accessors are derived from the
    samples so downstream callers keep their existing API.

    Attributes:
        group: Which group (control or treatment).
        agent_count: Number of agents in this group.
        quality_samples: Per-agent quality scores (0-10).
        success_samples: Per-agent success rates (0-1).
        spend_samples: Per-agent spend values (display currency).
        collected_at: When these metrics were collected.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    group: ABTestGroup
    agent_count: int = Field(ge=0)
    quality_samples: tuple[float, ...] = ()
    success_samples: tuple[float, ...] = ()
    spend_samples: tuple[float, ...] = ()
    collected_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def observation_count(self) -> int:
        """Number of metric samples collected (tuples are aligned)."""
        return len(self.quality_samples)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def avg_quality_score(self) -> float:
        """Mean of ``quality_samples``; ``0.0`` when empty."""
        if not self.quality_samples:
            return 0.0
        return math.fsum(self.quality_samples) / len(self.quality_samples)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def avg_success_rate(self) -> float:
        """Mean of ``success_samples``; ``0.0`` when empty."""
        if not self.success_samples:
            return 0.0
        return math.fsum(self.success_samples) / len(self.success_samples)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_spend(self) -> float:
        """Sum of ``spend_samples``; ``0.0`` when empty."""
        return math.fsum(self.spend_samples)

    @model_validator(mode="after")
    def _validate_sample_alignment(self) -> Self:
        """Sample tuples must be the same length."""
        n = len(self.quality_samples)
        if not (len(self.success_samples) == n and len(self.spend_samples) == n):
            msg = (
                "quality_samples, success_samples, and spend_samples "
                f"must be aligned; got lengths {n}, "
                f"{len(self.success_samples)}, {len(self.spend_samples)}"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_observations_require_agents(self) -> Self:
        """``observation_count`` must not exceed ``agent_count``.

        Each agent contributes at most one sample per metric per
        observation window, so a tuple longer than ``agent_count``
        means the producer double-counted and would inflate Welch's
        effective sample size. The ``agent_count == 0`` case is
        subsumed: any positive observation_count fails this check.
        """
        if self.observation_count > self.agent_count:
            msg = (
                f"observation_count ({self.observation_count}) cannot "
                f"exceed agent_count ({self.agent_count})"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_sample_bounds(self) -> Self:
        """Quality and success samples must live in their valid ranges."""
        for q in self.quality_samples:
            if not 0.0 <= q <= _MAX_QUALITY:
                msg = f"quality_samples must be in [0, 10]; got {q}"
                raise ValueError(msg)
        for s in self.success_samples:
            if not 0.0 <= s <= 1.0:
                msg = f"success_samples must be in [0, 1]; got {s}"
                raise ValueError(msg)
        for spend in self.spend_samples:
            if spend < 0.0:
                msg = f"spend_samples must be non-negative; got {spend}"
                raise ValueError(msg)
        return self


class ABTestComparison(BaseModel):
    """Result of comparing control vs treatment group metrics.

    Attributes:
        verdict: Outcome of the comparison.
        control_metrics: Metrics from the control group.
        treatment_metrics: Metrics from the treatment group.
        effect_size: Normalized improvement ratio proxy
            (None if insufficient data).
        p_value: Statistical significance proxy
            (None if insufficient data).
        regressed_metrics: Names of metrics where treatment was worse.
        compared_at: When the comparison was performed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    verdict: ABTestVerdict
    control_metrics: GroupMetrics
    treatment_metrics: GroupMetrics
    effect_size: float | None = None
    p_value: float | None = None
    regressed_metrics: tuple[NotBlankStr, ...] = ()
    compared_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )

    @model_validator(mode="after")
    def _validate_regression_has_metrics(self) -> Self:
        """Treatment regressions must identify which metrics regressed."""
        if (
            self.verdict == ABTestVerdict.TREATMENT_REGRESSED
            and not self.regressed_metrics
        ):
            msg = "treatment regressions must identify regressed_metrics"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_winner_has_stats(self) -> Self:
        """Winner verdicts must include effect_size and p_value."""
        if self.verdict in (
            ABTestVerdict.TREATMENT_WINS,
            ABTestVerdict.CONTROL_WINS,
        ) and (self.effect_size is None or self.p_value is None):
            msg = "winner verdicts must include effect_size and p_value"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_statistic_bounds(self) -> Self:
        """Statistical fields must be in valid ranges."""
        if self.p_value is not None and not 0.0 <= self.p_value <= 1.0:
            msg = "p_value must be in [0.0, 1.0]"
            raise ValueError(msg)
        if self.effect_size is not None and self.effect_size < 0.0:
            msg = "effect_size must be non-negative"
            raise ValueError(msg)
        return self
