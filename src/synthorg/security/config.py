"""Security configuration models.

Defines ``SecurityConfig`` (the top-level security configuration),
``RuleEngineConfig``, ``SecurityPolicyRule``, and
``OutputScanPolicyType`` for output scan response policy selection.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import ActionType, ApprovalRiskLevel
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.security.models import SecurityVerdictType
from synthorg.security.policy_engine.config import SecurityPolicyConfig


class SecurityEnforcementMode(StrEnum):
    """Security enforcement mode for the SecOps service.

    Controls whether security verdicts are enforced, logged only
    (shadow mode for calibration), or fully disabled.

    Members:
        ACTIVE: Full enforcement -- verdicts are applied as-is.
        SHADOW: Shadow mode -- full evaluation pipeline runs and
            audit entries are recorded, but blocking verdicts (DENY,
            ESCALATE) are converted to ALLOW.  Used for pre-deployment
            calibration of risk budgets.
        DISABLED: Security subsystem is disabled -- no evaluation,
            always ALLOW.
    """

    ACTIVE = "active"
    SHADOW = "shadow"
    DISABLED = "disabled"


class OutputScanPolicyType(StrEnum):
    """Declarative output scan policy selection.

    Used in ``SecurityConfig`` to select the output scan response
    policy at config time.  Runtime constructor injection is also
    supported for full flexibility.

    Members:
        REDACT: Return redacted content (scanner-level redaction).
        WITHHOLD: Clear redacted content, forcing fail-closed.
        LOG_ONLY: Log findings but pass output through.
        AUTONOMY_TIERED: Delegate based on effective autonomy level
            (default -- falls back to ``REDACT`` when no autonomy
            is configured).
    """

    REDACT = "redact"
    WITHHOLD = "withhold"
    LOG_ONLY = "log_only"
    AUTONOMY_TIERED = "autonomy_tiered"


class VerdictReasonVisibility(StrEnum):
    """Controls how much of the LLM evaluator's reason is visible to agents.

    Attributes:
        FULL: Return the full LLM reason to the agent.
        GENERIC: Return a generic denial/escalation message.
        CATEGORY: Return verdict type and risk level only.
    """

    FULL = "full"
    GENERIC = "generic"
    CATEGORY = "category"


class ArgumentTruncationStrategy(StrEnum):
    """How to truncate large tool arguments for the LLM security prompt.

    Attributes:
        WHOLE_STRING: Truncate the serialized JSON at a character limit.
        PER_VALUE: Truncate each argument value individually before
            serialization, preserving all key names.
        KEYS_AND_VALUES: Include all keys with individually capped
            values (explicit about key preservation).
    """

    WHOLE_STRING = "whole_string"
    PER_VALUE = "per_value"
    KEYS_AND_VALUES = "keys_and_values"


class LlmFallbackErrorPolicy(StrEnum):
    """What to do when the LLM security evaluation fails.

    Attributes:
        USE_RULE_VERDICT: Fall back to the original rule engine verdict.
        ESCALATE: Send the action to the human approval queue.
        DENY: Deny the action (fail-closed).
    """

    USE_RULE_VERDICT = "use_rule_verdict"
    ESCALATE = "escalate"
    DENY = "deny"


class LlmFallbackConfig(BaseModel):
    """Configuration for LLM-based security evaluation fallback.

    When enabled, actions that the rule engine cannot classify
    (no rule matched, low confidence) are routed to an LLM from
    a different provider family for cross-validation.

    Attributes:
        enabled: Whether LLM fallback is active.
        model: Explicit model ID for security evaluation.  When
            ``None``, the evaluator picks the first model from
            the selected provider (cross-family preferred,
            same-family fallback).
        timeout_seconds: Maximum time for the LLM call.
        max_input_tokens: Token budget cap for security eval prompts.
        on_error: Policy when the LLM call fails.
        reason_visibility: How much of the LLM reason is visible
            to the evaluated agent.
        argument_truncation: Strategy for truncating large tool
            arguments in the LLM prompt.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = False
    model: NotBlankStr | None = None
    timeout_seconds: float = Field(default=10.0, gt=0.0)
    max_input_tokens: int = Field(default=2000, gt=0)
    on_error: LlmFallbackErrorPolicy = LlmFallbackErrorPolicy.ESCALATE
    reason_visibility: VerdictReasonVisibility = VerdictReasonVisibility.GENERIC
    argument_truncation: ArgumentTruncationStrategy = (
        ArgumentTruncationStrategy.PER_VALUE
    )


class SecurityPolicyRule(BaseModel):
    """A single configurable security policy rule.

    Attributes:
        name: Rule name (used in matched_rules lists).
        description: Human-readable description.
        action_types: Action types this rule applies to (``category:action``).
        verdict: Verdict to return when rule matches.
        risk_level: Risk level to assign.
        enabled: Whether this rule is active.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr
    description: str = ""
    action_types: tuple[str, ...] = ()
    verdict: SecurityVerdictType = SecurityVerdictType.DENY
    risk_level: ApprovalRiskLevel = ApprovalRiskLevel.MEDIUM
    enabled: bool = True

    @model_validator(mode="after")
    def _check_action_type_format(self) -> Self:
        """Validate that action_types entries use ``category:action`` format.

        Requires exactly one colon with non-empty, non-whitespace
        segments on each side.
        """
        for at in self.action_types:
            parts = at.split(":")
            if len(parts) != 2:  # noqa: PLR2004
                msg = (
                    f"action_type {at!r} in policy {self.name!r} must "
                    "contain exactly one ':' (category:action)"
                )
                raise ValueError(msg)
            category, action = parts
            if not category.strip() or not action.strip():
                msg = (
                    f"action_type {at!r} in policy {self.name!r} has "
                    "empty or whitespace-only category or action segment"
                )
                raise ValueError(msg)
        return self


class RuleEngineConfig(BaseModel):
    """Configuration for the synchronous rule engine.

    Attributes:
        credential_patterns_enabled: Detect credentials in arguments.
        data_leak_detection_enabled: Detect sensitive file paths / PII.
        destructive_op_detection_enabled: Detect destructive operations.
        path_traversal_detection_enabled: Detect path traversal attacks.
        max_argument_length: Maximum argument string length for scanning.
        custom_allow_bypasses_detectors: When ``True``, custom ALLOW
            policies are placed before detectors, allowing them to
            short-circuit security scanning.  When ``False`` (default),
            custom policies are placed after all detectors so security
            scanning always runs first.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    credential_patterns_enabled: bool = True
    data_leak_detection_enabled: bool = True
    destructive_op_detection_enabled: bool = True
    path_traversal_detection_enabled: bool = True
    max_argument_length: int = Field(default=100_000, gt=0)
    custom_allow_bypasses_detectors: bool = False


class SafetyClassifierConfig(BaseModel):
    """Configuration for the two-stage safety classifier at approval gates.

    When enabled, escalated actions are processed through two stages:
    Stage 1 strips PII, secrets, and internal IDs from the reviewer
    view.  Stage 2 runs an LLM classifier to categorize the action
    as safe, suspicious, or blocked.

    Attributes:
        enabled: Whether the safety classifier is active.
        model: Explicit model ID for classification.  When ``None``,
            the classifier picks the first model from the selected
            provider (cross-family preferred, same-family fallback).
        timeout_seconds: Maximum time for the LLM classification call.
        max_input_tokens: Token budget cap for classification prompts.
        auto_reject_blocked: Automatically reject actions classified
            as BLOCKED (returns DENY verdict without creating an
            approval item).
        max_consecutive_denials: Maximum consecutive denials before
            escalation to human review.  Used by ``DenialTracker``.
        max_total_denials: Maximum total denials across the agent's
            lifetime before escalation.  Used by ``DenialTracker``.
        safe_tool_categories: Action types that bypass the safety
            classifier entirely (permission tier: SAFE_TOOL).
            Matched against the ``action_type`` field of the
            security context.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = False
    model: NotBlankStr | None = None
    timeout_seconds: float = Field(default=10.0, gt=0.0)
    max_input_tokens: int = Field(default=2000, gt=0)
    auto_reject_blocked: bool = True
    max_consecutive_denials: int = Field(default=3, ge=1)
    max_total_denials: int = Field(default=20, ge=1)
    safe_tool_categories: tuple[str, ...] = ("code:read", "docs:write")


class UncertaintyCheckConfig(BaseModel):
    """Configuration for cross-provider uncertainty checks.

    When enabled, the same prompt is sent to multiple providers and
    the responses are compared via keyword overlap and TF-IDF cosine
    similarity.  Low agreement produces a low confidence score,
    signaling potential hallucination.

    Attributes:
        enabled: Whether the uncertainty check is active.
        model_ref: Model alias to resolve via
            ``ModelResolver.resolve_all`` for multi-provider
            candidates.  When ``None``, the check is skipped.
        min_providers: Minimum number of providers required to run
            the check.  If fewer candidates are available the check
            is skipped and confidence defaults to 1.0.
        low_confidence_threshold: Confidence scores below this
            threshold are flagged as potentially hallucinated.
        timeout_seconds: Maximum time per provider call.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = False
    model_ref: NotBlankStr | None = None
    min_providers: int = Field(default=2, ge=2)
    low_confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    timeout_seconds: float = Field(default=15.0, gt=0.0)


class SecurityConfig(BaseModel):
    """Top-level security configuration.

    Attributes:
        enabled: Master switch for the security subsystem.
        enforcement_mode: Security enforcement mode
            (active/shadow/disabled).
        rule_engine: Rule engine configuration.
        llm_fallback: LLM-based fallback for uncertain evaluations.
        audit_enabled: Whether to record audit entries.
        post_tool_scanning_enabled: Scan tool output for secrets.
        hard_deny_action_types: Action types always denied.
        auto_approve_action_types: Action types always approved.
        output_scan_policy_type: Output scan response policy
            (default: ``AUTONOMY_TIERED``).
        custom_policies: User-defined policy rules.
        policy_engine: Runtime policy engine configuration
            (Cedar-based pre-execution gate, opt-in).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = True
    enforcement_mode: SecurityEnforcementMode = Field(
        default=SecurityEnforcementMode.ACTIVE,
        description="Security enforcement mode (active/shadow/disabled)",
    )
    rule_engine: RuleEngineConfig = Field(
        default_factory=RuleEngineConfig,
    )
    llm_fallback: LlmFallbackConfig = Field(
        default_factory=LlmFallbackConfig,
    )
    audit_enabled: bool = True
    post_tool_scanning_enabled: bool = True
    hard_deny_action_types: tuple[str, ...] = (
        ActionType.DEPLOY_PRODUCTION,
        ActionType.DB_ADMIN,
        ActionType.ORG_FIRE,
    )
    auto_approve_action_types: tuple[str, ...] = (
        ActionType.CODE_READ,
        ActionType.DOCS_WRITE,
    )
    output_scan_policy_type: OutputScanPolicyType = OutputScanPolicyType.AUTONOMY_TIERED
    custom_policies: tuple[SecurityPolicyRule, ...] = ()
    safety_classifier: SafetyClassifierConfig = Field(
        default_factory=SafetyClassifierConfig,
    )
    uncertainty_check: UncertaintyCheckConfig = Field(
        default_factory=UncertaintyCheckConfig,
    )
    policy_engine: SecurityPolicyConfig = Field(
        default_factory=SecurityPolicyConfig,
        description="Runtime policy engine configuration",
    )
    audit_retention_days: int = Field(
        default=730,
        ge=0,
        le=36_500,
        description=(
            "Days to retain audit_entries before automatic purge."
            " 0 disables purging (unbounded). Default 730 (2 years)."
        ),
    )
    retention_cleanup_paused: bool = Field(
        default=False,
        description=(
            "Pause flag for the audit retention purge loop. When"
            " True the loop stays resident but every tick"
            " short-circuits."
        ),
    )

    @model_validator(mode="after")
    def _check_disjoint_action_types(self) -> SecurityConfig:
        """Reject overlapping hard-deny and auto-approve action types."""
        deny_set = set(self.hard_deny_action_types)
        approve_set = set(self.auto_approve_action_types)
        overlap = deny_set & approve_set
        if overlap:
            msg = (
                f"hard_deny_action_types and auto_approve_action_types "
                f"must not overlap: {sorted(overlap)}"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_unique_custom_policy_names(self) -> SecurityConfig:
        """Reject duplicate custom policy names."""
        seen: set[str] = set()
        for policy in self.custom_policies:
            if policy.name in seen:
                msg = f"duplicate custom policy name {policy.name!r}"
                raise ValueError(msg)
            seen.add(policy.name)
        return self

    @model_validator(mode="after")
    def _check_no_allow_or_escalate_bypass(self) -> SecurityConfig:
        """Reject ALLOW/ESCALATE policies when bypass mode is enabled.

        With ``custom_allow_bypasses_detectors=True``, custom policies
        are placed before detectors.  Both ALLOW and ESCALATE verdicts
        short-circuit the rule engine, so either would skip all
        security detectors (credential, path traversal, etc.).  Only
        DENY policies are safe in bypass position.
        """
        if not self.rule_engine.custom_allow_bypasses_detectors:
            return self
        unsafe_verdicts = {
            SecurityVerdictType.ALLOW,
            SecurityVerdictType.ESCALATE,
        }
        unsafe_policies = [
            p.name
            for p in self.custom_policies
            if p.enabled and p.verdict in unsafe_verdicts
        ]
        if unsafe_policies:
            msg = (
                "custom_allow_bypasses_detectors=True cannot be used "
                "with ALLOW or ESCALATE custom policies (would skip "
                "all security detectors): "
                f"{sorted(unsafe_policies)}"
            )
            raise ValueError(msg)
        return self
