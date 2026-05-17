"""Domain models for cross-deployment analytics.

Defines anonymized event payloads, aggregated patterns, and
threshold recommendations. All models are frozen with
``allow_inf_nan=False``.
"""

import datetime
import re
from typing import Annotated, Literal, Self

from annotated_types import Ge
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001

NonNegativeInt = Annotated[int, Ge(0)]

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class AnonymizedOutcomeEvent(BaseModel):
    """Anonymized outcome event for cross-deployment analytics.

    Single flat model covering both proposal decisions and rollout
    results. Fields irrelevant to the event type are ``None``.

    No PII survives: free text is dropped, UUIDs are salted-hashed,
    timestamps are coarsened to day granularity.

    Attributes:
        schema_version: Wire format version for forward compat.
        deployment_id: Salted SHA-256 hash of deployment UUID.
        event_type: Whether this records a decision or rollout.
        timestamp: ISO 8601 date (day granularity, no time).
        altitude: Proposal altitude enum value.
        source_rule: Built-in rule name or ``"custom"`` for
            user-defined rules. None if no rule triggered.
        decision: Human decision (proposal_decision events only).
        confidence: Proposal confidence at decision time.
        rollout_outcome: Rollout outcome enum value
            (rollout_result events only).
        regression_verdict: Regression detection result.
        observation_hours: Observation window duration in hours.
        enabled_altitudes: Which altitudes are enabled in the
            deployment's config (categorical, not config values).
        industry_tag: Optional user-provided industry category.
        sdk_version: SynthOrg version string.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    schema_version: Literal["1"] = "1"
    deployment_id: NotBlankStr
    event_type: Literal["proposal_decision", "rollout_result"]
    timestamp: NotBlankStr  # ISO 8601 date (YYYY-MM-DD)
    altitude: NotBlankStr

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp_format(cls, v: str) -> str:
        """Enforce valid ISO 8601 date (YYYY-MM-DD) with calendar check."""
        if not _ISO_DATE_RE.match(v):
            msg = f"timestamp must be ISO date (YYYY-MM-DD), got '{v}'"
            raise ValueError(msg)
        try:
            datetime.date.fromisoformat(v)
        except ValueError as exc:
            msg = f"timestamp is not a valid calendar date: '{v}'"
            raise ValueError(msg) from exc
        return v

    source_rule: NotBlankStr | None = None
    decision: Literal["approved", "rejected"] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    rollout_outcome: NotBlankStr | None = None
    regression_verdict: NotBlankStr | None = None
    observation_hours: float | None = Field(default=None, ge=0.0)
    enabled_altitudes: tuple[NotBlankStr, ...] = ()
    industry_tag: NotBlankStr | None = None
    sdk_version: NotBlankStr

    @model_validator(mode="after")
    def _validate_event_type_fields(self) -> Self:
        """Enforce proposal_decision XOR rollout_result field presence."""
        if self.event_type == "proposal_decision":
            if self.decision is None:
                msg = "proposal_decision events require decision"
                raise ValueError(msg)
            if self.rollout_outcome is not None:
                msg = "proposal_decision cannot have rollout_outcome"
                raise ValueError(msg)
            if self.regression_verdict is not None:
                msg = "proposal_decision cannot have regression_verdict"
                raise ValueError(msg)
            if self.observation_hours is not None:
                msg = "proposal_decision cannot have observation_hours"
                raise ValueError(msg)
        elif self.event_type == "rollout_result":
            if self.rollout_outcome is None:
                msg = "rollout_result events require rollout_outcome"
                raise ValueError(msg)
            if self.decision is not None:
                msg = "rollout_result cannot have decision"
                raise ValueError(msg)
            if self.confidence is not None:
                msg = "rollout_result cannot have confidence"
                raise ValueError(msg)
        return self


class EventBatch(BaseModel):
    """Batch of anonymized events for transport.

    Attributes:
        events: Tuple of anonymized outcome events.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    events: tuple[AnonymizedOutcomeEvent, ...] = Field(max_length=1000)


class AggregatedPattern(BaseModel):
    """Cross-deployment pattern identified from aggregated events.

    Represents a ``(source_rule, altitude)`` combination observed
    across multiple deployments with computed statistics.

    Attributes:
        source_rule: Rule name (built-in or ``"custom"``).
        altitude: Proposal altitude.
        deployment_count: Unique deployments that observed this.
        total_events: Total events in this pattern group.
        decision_count: Number of proposal_decision events.
        approval_rate: Cross-deployment approval rate (0-1).
        success_rate: Cross-deployment rollout success rate (0-1).
        avg_confidence: Mean confidence at decision time.
        avg_observation_hours: Mean observation window (rollout
            events only, None if no rollout events).
        industry_breakdown: Sorted (industry_tag, count) pairs.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    source_rule: NotBlankStr
    altitude: NotBlankStr
    deployment_count: int = Field(ge=1)
    total_events: int = Field(ge=1)
    decision_count: int = Field(ge=0)
    approval_rate: float = Field(ge=0.0, le=1.0)
    success_rate: float = Field(ge=0.0, le=1.0)
    avg_confidence: float = Field(ge=0.0, le=1.0)
    avg_observation_hours: float | None = Field(default=None, ge=0.0)
    industry_breakdown: tuple[tuple[NotBlankStr, NonNegativeInt], ...] = ()


class ThresholdRecommendation(BaseModel):
    """Recommended threshold adjustment from cross-deployment data.

    Generated when a pattern shows consistent outcomes across
    enough deployments to suggest the default threshold should
    be adjusted.

    Attributes:
        rule_name: Built-in rule this recommendation targets.
        metric_name: Config field path for the threshold
            (e.g., ``"regression.quality_drop_threshold"``).
        current_default: Current default threshold value.
        recommended_value: Suggested new threshold value.
        confidence: Confidence in this recommendation (0-1).
        based_on_deployments: Unique deployments in the data.
        based_on_observations: Total events in the data.
        rationale: Human-readable explanation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    rule_name: NotBlankStr
    metric_name: NotBlankStr
    current_default: float
    recommended_value: float
    confidence: float = Field(ge=0.0, le=1.0)
    based_on_deployments: int = Field(ge=1)
    based_on_observations: int = Field(ge=1)
    rationale: NotBlankStr
