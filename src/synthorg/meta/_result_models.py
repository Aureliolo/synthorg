"""Signal-match, guard, and outcome models for the meta-loop.

Rule matches and guard results that gate a proposal, plus the rollout /
cycle / apply / CI-validation / regression outcome records that the
meta-loop produces. Each record enforces its own consistency invariant
(e.g. a regressed rollout must carry a regression verdict).
"""

from datetime import UTC, datetime
from typing import Self
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    computed_field,
    model_validator,
)

from synthorg.core.types import NotBlankStr
from synthorg.meta._model_enums import (
    GuardVerdict,
    ProposalAltitude,
    RegressionVerdict,
    RolloutOutcome,
    RuleSeverity,
)
from synthorg.meta._proposal_models import ImprovementProposal


class RuleMatch(BaseModel):
    """Result of a signal rule detecting a pattern.

    Attributes:
        rule_name: Name of the rule that fired.
        severity: How urgent this match is.
        description: Human-readable explanation.
        signal_context: Specific data that triggered the rule.
        suggested_altitudes: Which strategies should generate proposals.
        matched_at: When the match was detected.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    rule_name: NotBlankStr
    severity: RuleSeverity
    description: NotBlankStr
    signal_context: dict[str, JsonValue] = Field(default_factory=dict)
    suggested_altitudes: tuple[ProposalAltitude, ...] = Field(min_length=1)
    matched_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )


class GuardResult(BaseModel):
    """Outcome of a guard evaluating a proposal.

    Attributes:
        guard_name: Name of the guard.
        verdict: Whether the proposal passed or was rejected.
        reason: Explanation (required on rejection).
        evaluated_at: When the evaluation happened.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    guard_name: NotBlankStr
    verdict: GuardVerdict
    reason: NotBlankStr | None = None
    evaluated_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )

    @model_validator(mode="after")
    def _validate_rejection_has_reason(self) -> Self:
        """Rejected verdicts must include a reason.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if self.verdict == GuardVerdict.REJECTED and not self.reason:
            msg = "rejected guard verdicts must include a reason"
            raise ValueError(msg)
        return self


class RolloutResult(BaseModel):
    """Outcome of a staged rollout.

    Attributes:
        proposal_id: Which proposal was rolled out.
        outcome: Final result.
        regression_verdict: Regression detection result (if checked).
        observation_hours_elapsed: How long the observation ran.
        details: Additional context about the rollout.
        completed_at: When the rollout finished.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    proposal_id: UUID
    outcome: RolloutOutcome
    regression_verdict: RegressionVerdict | None = None
    observation_hours_elapsed: float = Field(ge=0.0)
    details: NotBlankStr | None = None
    completed_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )

    @model_validator(mode="after")
    def _validate_regressed_has_verdict(self) -> Self:
        """Regressed outcomes must include a regression verdict.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if self.outcome == RolloutOutcome.REGRESSED and not self.regression_verdict:
            msg = "regressed outcomes must include regression_verdict"
            raise ValueError(msg)
        return self


class ImprovementCycleResult(BaseModel):
    """Outcome of an explicit ``trigger_cycle`` invocation.

    Returned by :meth:`SelfImprovementService.trigger_cycle`. Wraps the
    proposals produced by an in-process cycle plus run metadata so MCP
    operators can identify the run, see how long it took, and inspect
    the proposals without making a second call.

    Attributes:
        cycle_id: Stable identifier for this trigger invocation.
        started_at: When the cycle began (UTC).
        completed_at: When the cycle finished (UTC); always >= started_at.
        proposals_count: Number of proposals returned (computed from
            ``proposals``; surfaced for telemetry consumers).
        proposals: Proposals produced by the cycle.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    cycle_id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
        description="Stable identifier for the trigger invocation",
    )
    started_at: AwareDatetime
    completed_at: AwareDatetime
    proposals: tuple[ImprovementProposal, ...] = ()

    @model_validator(mode="after")
    def _validate_completion_ordering(self) -> Self:
        """Ensure completed_at is not earlier than started_at.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if self.completed_at < self.started_at:
            msg = (
                f"completed_at ({self.completed_at}) must be at or after "
                f"started_at ({self.started_at})"
            )
            raise ValueError(msg)
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def proposals_count(self) -> int:
        """Number of proposals produced by the cycle.

        Returns:
            Resulting integer.
        """
        return len(self.proposals)


class ApplyResult(BaseModel):
    """Outcome of applying a proposal change.

    Attributes:
        success: Whether the apply succeeded.
        error_message: Error description on failure.
        changes_applied: Number of individual changes applied.
        applied_at: When the apply completed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    success: bool
    error_message: NotBlankStr | None = None
    changes_applied: int = Field(ge=0)
    applied_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )

    @model_validator(mode="after")
    def _validate_failure_has_message(self) -> Self:
        """Failed applies must include an error message.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if not self.success and not self.error_message:
            msg = "failed apply results must include an error_message"
            raise ValueError(msg)
        return self


class CIValidationResult(BaseModel):
    """Outcome of running CI checks against proposed code changes.

    Attributes:
        passed: Whether all checks passed.
        lint_passed: Whether ruff lint passed.
        typecheck_passed: Whether mypy type-check passed.
        tests_passed: Whether pytest tests passed.
        errors: Error descriptions from failed steps.
        duration_seconds: Total wall-clock time for validation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    passed: bool
    lint_passed: bool
    typecheck_passed: bool
    tests_passed: bool
    errors: tuple[NotBlankStr, ...] = ()
    duration_seconds: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _validate_passed_consistent(self) -> Self:
        """Passed must exactly match the conjunction of sub-checks.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        all_ok = self.lint_passed and self.typecheck_passed and self.tests_passed
        if self.passed != all_ok:
            msg = "passed must equal the conjunction of all sub-checks"
            raise ValueError(msg)
        if self.passed and self.errors:
            msg = "passed CI validations must not include errors"
            raise ValueError(msg)
        if not self.passed and not self.errors:
            msg = "failed CI validations must include at least one error"
            raise ValueError(msg)
        return self


class RegressionThresholds(BaseModel):
    """Configurable thresholds for regression detection.

    All values are fractional (0.10 = 10% degradation).

    Attributes:
        quality_drop: Max acceptable quality score drop.
        cost_increase: Max acceptable cost increase.
        error_rate_increase: Max acceptable error rate increase.
        success_rate_drop: Max acceptable success rate drop.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    quality_drop: float = Field(default=0.10, ge=0.0, le=1.0)
    cost_increase: float = Field(default=0.20, ge=0.0, le=1.0)
    error_rate_increase: float = Field(default=0.15, ge=0.0, le=1.0)
    success_rate_drop: float = Field(default=0.10, ge=0.0, le=1.0)


class RegressionResult(BaseModel):
    """Outcome of a regression detection check.

    Attributes:
        verdict: Whether regression was detected.
        breached_metric: Which metric breached (if any).
        baseline_value: Metric value before the change.
        current_value: Metric value after the change.
        threshold: Threshold that was breached.
        p_value: Statistical p-value (for statistical checks).
        checked_at: When the check was performed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    verdict: RegressionVerdict
    breached_metric: NotBlankStr | None = None
    baseline_value: float | None = None
    current_value: float | None = None
    threshold: float | None = None
    p_value: float | None = None
    checked_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )

    @model_validator(mode="after")
    def _validate_breach_has_details(self) -> Self:
        """Threshold breaches must include metric details.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if self.verdict == RegressionVerdict.THRESHOLD_BREACH:
            if not self.breached_metric:
                msg = "threshold breaches must identify the breached metric"
                raise ValueError(msg)
            if self.baseline_value is None or self.current_value is None:
                msg = "threshold breaches must include baseline and current values"
                raise ValueError(msg)
        if (
            self.verdict == RegressionVerdict.STATISTICAL_REGRESSION
            and self.p_value is None
        ):
            msg = "statistical regressions must include p_value"
            raise ValueError(msg)
        return self
