"""Domain models for the self-improving company meta-loop.

Defines improvement proposals, rollback plans, signal snapshots,
rule matches, guard results, and rollout outcomes that flow through
the meta-improvement pipeline.
"""

from datetime import UTC, datetime
from enum import StrEnum
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

# ── Enums ──────────────────────────────────────────────────────────


class ProposalAltitude(StrEnum):
    """Altitude of change a proposal targets."""

    CONFIG_TUNING = "config_tuning"
    ARCHITECTURE = "architecture"
    PROMPT_TUNING = "prompt_tuning"
    CODE_MODIFICATION = "code_modification"


class ProposalStatus(StrEnum):
    """Lifecycle status of an improvement proposal."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLYING = "applying"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    REGRESSED = "regressed"


class RolloutStrategyType(StrEnum):
    """How an approved proposal is deployed."""

    BEFORE_AFTER = "before_after"
    CANARY = "canary"
    AB_TEST = "ab_test"


class EvolutionMode(StrEnum):
    """How prompt tuning proposals interact with agent evolution."""

    ORG_WIDE = "org_wide"
    OVERRIDE = "override"
    ADVISORY = "advisory"


class RuleSeverity(StrEnum):
    """Severity of a rule match."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class CodeOperation(StrEnum):
    """Type of source file change in a code modification proposal."""

    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"


class GuardVerdict(StrEnum):
    """Outcome of a guard evaluation."""

    PASSED = "passed"
    REJECTED = "rejected"


class RolloutOutcome(StrEnum):
    """Final outcome of a rollout."""

    SUCCESS = "success"
    REGRESSED = "regressed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class RegressionVerdict(StrEnum):
    """Result of a regression check."""

    NO_REGRESSION = "no_regression"
    THRESHOLD_BREACH = "threshold_breach"
    STATISTICAL_REGRESSION = "statistical_regression"
    INSUFFICIENT_DATA = "insufficient_data"


# ── Rollback models ────────────────────────────────────────────────


class RollbackOperation(BaseModel):
    """A single inverse operation in a rollback plan.

    Attributes:
        operation_type: Kind of reversal (revert_config, delete_role, etc.).
        target: What to revert (config path, role name, etc.).
        previous_value: Value to restore (None for deletions).
        description: Human-readable description.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    operation_type: NotBlankStr
    target: NotBlankStr
    previous_value: JsonValue = None
    description: NotBlankStr


class RollbackPlan(BaseModel):
    """Concrete plan for reverting an improvement proposal.

    Attributes:
        operations: Ordered inverse operations.
        dependencies: Proposal IDs that must rollback first.
        validation_check: Post-rollback assertion description.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    operations: tuple[RollbackOperation, ...] = Field(min_length=1)
    dependencies: tuple[UUID, ...] = ()
    validation_check: NotBlankStr


# ── Change models (altitude-specific) ─────────────────────────────


class ConfigChange(BaseModel):
    """A single config field change.

    Attributes:
        path: JSON-path to the config field (e.g. ``budget.total_monthly``).
        old_value: Current value.
        new_value: Proposed value.
        description: Why this change is proposed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    path: NotBlankStr
    old_value: JsonValue = None
    new_value: JsonValue = None
    description: NotBlankStr


class ArchitectureChange(BaseModel):
    """A structural change to the organization.

    Attributes:
        operation: Type of change (create_role, create_department,
            modify_workflow, remove_role, etc.).
        target_name: Name of the entity being changed.
        payload: Structured change data (operation-specific).
        description: Why this change is proposed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    operation: NotBlankStr
    target_name: NotBlankStr
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    description: NotBlankStr


class PromptChange(BaseModel):
    """An org-wide prompt policy change.

    Attributes:
        principle_text: The constitutional principle to inject.
        target_scope: Who this applies to (role name, department, or ``all``).
        evolution_mode: How this interacts with per-agent evolution.
        description: Why this change is proposed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    principle_text: NotBlankStr
    target_scope: NotBlankStr
    evolution_mode: EvolutionMode = EvolutionMode.ORG_WIDE
    description: NotBlankStr


class CodeChange(BaseModel):
    """A proposed change to a framework source file.

    Uses full file content rather than line-level diffs: LLMs produce
    complete content reliably, framework files are < 800 lines by
    convention, and git shows the actual diff on the PR.

    Attributes:
        file_path: Relative path from project root.
        operation: Type of file change (create, modify, delete).
        old_content: Current file content (empty for create; captured
            at proposal time for rollback on modify/delete).
        new_content: Proposed file content (empty for delete).
        description: What this change does.
        reasoning: Why this change improves the system.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    file_path: NotBlankStr
    operation: CodeOperation
    old_content: str = ""
    new_content: str = ""
    description: NotBlankStr
    reasoning: NotBlankStr

    @model_validator(mode="after")
    def _validate_content_for_operation(self) -> Self:
        """Ensure content fields match the operation type."""
        _CODE_CHANGE_VALIDATORS[self.operation](self)
        return self


def _validate_create(change: CodeChange) -> None:
    if change.old_content:
        msg = "create operations must have empty old_content"
        raise ValueError(msg)
    if not change.new_content:
        msg = "create operations must have non-empty new_content"
        raise ValueError(msg)


def _validate_modify(change: CodeChange) -> None:
    if not change.old_content:
        msg = "modify operations must have non-empty old_content"
        raise ValueError(msg)
    if not change.new_content:
        msg = "modify operations must have non-empty new_content"
        raise ValueError(msg)
    if change.old_content == change.new_content:
        msg = "modify operations must change the content"
        raise ValueError(msg)


def _validate_delete(change: CodeChange) -> None:
    if not change.old_content:
        msg = "delete operations must have non-empty old_content"
        raise ValueError(msg)
    if change.new_content:
        msg = "delete operations must have empty new_content"
        raise ValueError(msg)


_CODE_CHANGE_VALIDATORS = {
    CodeOperation.CREATE: _validate_create,
    CodeOperation.MODIFY: _validate_modify,
    CodeOperation.DELETE: _validate_delete,
}


# ── Proposal rationale ─────────────────────────────────────────────


class ProposalRationale(BaseModel):
    """Evidence and reasoning behind an improvement proposal.

    Attributes:
        signal_summary: Key signals that motivated this proposal.
        pattern_detected: The pattern or issue identified.
        expected_impact: What improvement is expected.
        confidence_reasoning: Why the confidence level was assigned.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    signal_summary: NotBlankStr
    pattern_detected: NotBlankStr
    expected_impact: NotBlankStr
    confidence_reasoning: NotBlankStr


# ── Improvement proposal ──────────────────────────────────────────


class ImprovementProposal(BaseModel):
    """A proposed improvement to the company deployment.

    Generated by an ``ImprovementStrategy``, validated by
    ``ProposalGuard``(s), and applied via a ``ProposalApplier``
    after mandatory human approval.

    Attributes:
        id: Unique proposal identifier.
        altitude: Which altitude of change this targets.
        title: Short human-readable title.
        description: Detailed description of the proposed change.
        rationale: Evidence and reasoning.
        config_changes: Config field changes (config_tuning altitude).
        architecture_changes: Structural changes (architecture altitude).
        prompt_changes: Prompt policy changes (prompt_tuning altitude).
        code_changes: Source file changes (code_modification altitude).
        rollback_plan: Concrete rollback plan.
        rollout_strategy: How to deploy the change.
        confidence: Strategy's confidence in this proposal (0-1).
        source_rule: Name of the rule that triggered this proposal.
        status: Lifecycle status.
        proposed_at: When the proposal was generated.
        decided_at: When the proposal was approved/rejected.
        decided_by: Who approved/rejected the proposal.
        decision_reason: Reason for approval/rejection.
        observation_window_hours: Post-apply observation window.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: UUID = Field(default_factory=uuid4)
    altitude: ProposalAltitude
    title: NotBlankStr
    description: NotBlankStr
    rationale: ProposalRationale
    config_changes: tuple[ConfigChange, ...] = ()
    architecture_changes: tuple[ArchitectureChange, ...] = ()
    prompt_changes: tuple[PromptChange, ...] = ()
    code_changes: tuple[CodeChange, ...] = ()
    rollback_plan: RollbackPlan
    rollout_strategy: RolloutStrategyType = RolloutStrategyType.BEFORE_AFTER
    confidence: float = Field(ge=0.0, le=1.0)
    source_rule: NotBlankStr | None = None
    status: ProposalStatus = ProposalStatus.PENDING
    proposed_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )
    decided_at: AwareDatetime | None = None
    decided_by: NotBlankStr | None = None
    decision_reason: NotBlankStr | None = None
    observation_window_hours: int = Field(default=48, ge=1)

    @model_validator(mode="after")
    def _validate_decision_consistency(self) -> Self:
        """Ensure decided_at/decided_by/decision_reason are all-or-nothing."""
        decided = (self.decided_at, self.decided_by, self.decision_reason)
        if any(decided) and not all(decided):
            msg = (
                "decided_at, decided_by, and decision_reason "
                "must all be set or all be None"
            )
            raise ValueError(msg)
        terminal = {
            ProposalStatus.APPROVED,
            ProposalStatus.REJECTED,
            ProposalStatus.APPLIED,
        }
        if self.status in terminal and not all(decided):
            msg = "non-pending proposals must include decision metadata"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_changes_match_altitude(self) -> Self:
        """Ensure only the declared altitude carries changes."""
        other_code = self.code_changes
        if self.altitude == ProposalAltitude.CONFIG_TUNING and (
            not self.config_changes
            or self.architecture_changes
            or self.prompt_changes
            or other_code
        ):
            msg = "config_tuning proposals must contain only config_changes"
            raise ValueError(msg)
        if self.altitude == ProposalAltitude.ARCHITECTURE and (
            not self.architecture_changes
            or self.config_changes
            or self.prompt_changes
            or other_code
        ):
            msg = "architecture proposals must contain only architecture_changes"
            raise ValueError(msg)
        if self.altitude == ProposalAltitude.PROMPT_TUNING and (
            not self.prompt_changes
            or self.config_changes
            or self.architecture_changes
            or other_code
        ):
            msg = "prompt_tuning proposals must contain only prompt_changes"
            raise ValueError(msg)
        if self.altitude == ProposalAltitude.CODE_MODIFICATION and (
            not self.code_changes
            or self.config_changes
            or self.architecture_changes
            or self.prompt_changes
        ):
            msg = "code_modification proposals must contain only code_changes"
            raise ValueError(msg)
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def change_count(self) -> int:
        """Total number of changes across all altitudes."""
        return (
            len(self.config_changes)
            + len(self.architecture_changes)
            + len(self.prompt_changes)
            + len(self.code_changes)
        )


# ── Rule match ─────────────────────────────────────────────────────


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


# ── Guard result ───────────────────────────────────────────────────


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
        """Rejected verdicts must include a reason."""
        if self.verdict == GuardVerdict.REJECTED and not self.reason:
            msg = "rejected guard verdicts must include a reason"
            raise ValueError(msg)
        return self


# ── Rollout result ─────────────────────────────────────────────────


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
        """Regressed outcomes must include a regression verdict."""
        if self.outcome == RolloutOutcome.REGRESSED and not self.regression_verdict:
            msg = "regressed outcomes must include regression_verdict"
            raise ValueError(msg)
        return self


# ── Improvement cycle result ──────────────────────────────────────


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
        """Ensure completed_at is not earlier than started_at."""
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
        """Number of proposals produced by the cycle."""
        return len(self.proposals)


# ── Apply result ───────────────────────────────────────────────────


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
        """Failed applies must include an error message."""
        if not self.success and not self.error_message:
            msg = "failed apply results must include an error_message"
            raise ValueError(msg)
        return self


# ── CI validation result ──────────────────────────────────────────


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
        """Passed must exactly match the conjunction of sub-checks."""
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


# ── Regression thresholds ──────────────────────────────────────────


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


# ── Regression result ──────────────────────────────────────────────


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
        """Threshold breaches must include metric details."""
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


# ── Signal summary models (re-exported from signal_models) ────────
# Moved to signal_models.py to keep models.py under 800 lines.
# All names remain importable from synthorg.meta.models via __all__.

from synthorg.meta.signal_models import (  # noqa: E402
    ErrorCategorySummary,
    EvolutionOutcomeSummary,
    MetricSummary,
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgScalingSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
    ScalingDecisionSummary,
    TrendDirection,
)

__all__ = [
    # Core models
    "ApplyResult",
    "ArchitectureChange",
    "CIValidationResult",
    "CodeChange",
    # Enums
    "CodeOperation",
    "ConfigChange",
    # Re-exported signal models
    "ErrorCategorySummary",
    "EvolutionMode",
    "EvolutionOutcomeSummary",
    "GuardResult",
    "GuardVerdict",
    "ImprovementCycleResult",
    "ImprovementProposal",
    "MetricSummary",
    "OrgBudgetSummary",
    "OrgCoordinationSummary",
    "OrgErrorSummary",
    "OrgEvolutionSummary",
    "OrgPerformanceSummary",
    "OrgScalingSummary",
    "OrgSignalSnapshot",
    "OrgTelemetrySummary",
    "PromptChange",
    "ProposalAltitude",
    "ProposalRationale",
    "ProposalStatus",
    "RegressionResult",
    "RegressionThresholds",
    "RegressionVerdict",
    "RollbackOperation",
    "RollbackPlan",
    "RolloutOutcome",
    "RolloutResult",
    "RolloutStrategyType",
    "RuleMatch",
    "RuleSeverity",
    "ScalingDecisionSummary",
    "TrendDirection",
]
