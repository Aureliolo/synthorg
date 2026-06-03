"""Security configuration models.

Defines ``SecurityConfig`` (the top-level security configuration),
``RuleEngineConfig``, ``SecurityPolicyRule``, and
``OutputScanPolicyType`` for output scan response policy selection.
"""

from enum import StrEnum
from typing import Any, ClassVar, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import ActionType, ApprovalRiskLevel
from synthorg.core.types import ModelTier, NotBlankStr
from synthorg.security.models import SecurityVerdictType
from synthorg.security.policy_engine.config import SecurityPolicyConfig
from synthorg.settings.definitions.security import (
    AUDIT_RETENTION_TICK_DEFAULT_SECONDS,
    AUDIT_RETENTION_TICK_MAX_SECONDS,
    AUDIT_RETENTION_TICK_MIN_SECONDS,
    RED_TEAM_TIMEOUT_DEFAULT_SECONDS,
    RED_TEAM_TIMEOUT_MAX_SECONDS,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    MirrorField,
    apply_settings_mirrors,
    parse_bool,
    parse_float,
    parse_int,
)


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
    # Sampling parameters for the security-evaluation completion call.
    # Pinned to deterministic defaults so verdicts stay reproducible
    # across runs; operators tuning this MUST re-run the golden eval
    # suite (``tests/prompts/golden/llm_security_evaluator``).
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)


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

        Returns:
            The validated policy.

        Raises:
            ValueError: If an entry lacks exactly one ':' or has an empty
                segment on either side.
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


class McpSelfConsumerMode(StrEnum):
    """Dispatch token for the agent -> SynthOrg-MCP self-consumer.

    ``DISABLED`` (default, safe) wires no bridge: a running agent
    cannot call SynthOrg's own MCP tools. ``TRUST_SCOPED`` exposes
    the MCP surface to the agent's tool invoker, scoped by the
    agent's earned trust level (ELEVATED gets the full capability
    set; everything below is restricted to the explicit
    ``read_tool_allowlist``).
    """

    DISABLED = "disabled"
    TRUST_SCOPED = "trust_scoped"


class RedTeamConfig(BaseModel):
    """Adversarial red-team gate configuration (opt-in subsystem).

    OFF by default. When ``enabled`` the gate fires as the last
    adversarial check before IN_REVIEW -> COMPLETED.

    Attributes:
        enabled: Master switch (``False`` builds no gate at boot).
        grounding_checker_kind: Grounding discriminator. ``"heuristic"``
            (default, LOW-capped stub) or ``"knowledge_substrate"`` (LLM
            entailment checker that escalates to HIGH; degrades when off).
        timeout_seconds: Per-evaluation cap on the inline AgentEngine.
        on_missing_deliverable: Posture when the gate is enabled but no
            deliverable is retrievable. ``"block"`` (default) fails
            closed (rework) so the gate never silently depends on flight
            recording being on; ``"skip"`` allows completion.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = False
    grounding_checker_kind: Literal["heuristic", "knowledge_substrate"] = "heuristic"
    timeout_seconds: float = Field(
        default=RED_TEAM_TIMEOUT_DEFAULT_SECONDS,
        gt=0.0,
        le=RED_TEAM_TIMEOUT_MAX_SECONDS,
    )
    on_missing_deliverable: Literal["block", "skip"] = "block"


VISION_TIMEOUT_DEFAULT_SECONDS: Final[float] = 60.0
VISION_TIMEOUT_MAX_SECONDS: Final[float] = 600.0
VISION_DEFAULT_COLOUR_TOLERANCE: Final[float] = 0.15


class VisionVerifierKind(StrEnum):
    """Discriminator selecting a concrete vision verifier strategy."""

    NOOP = "noop"
    HEURISTIC = "heuristic"
    LLM_VISION = "llm_vision"


class VisionVerifyConfig(BaseModel):
    """Vision verifier gate configuration (opt-in subsystem).

    The vision gate is OFF by default. When ``enabled``, it fires as an
    adversarial check after ``ReviewPipeline`` PASS and the red-team
    gate, before the deliverable transitions IN_REVIEW -> COMPLETED.

    Attributes:
        enabled: Master switch. When ``False`` (default), no vision gate
            is constructed at boot and the ReviewGateService
            short-circuits as if the gate were absent.
        verifier_kind: Strategy discriminator. ``noop`` is inert,
            ``heuristic`` runs deterministic colour / rule checks,
            ``llm_vision`` calls a multimodal model.
        model_tier: Provider tier resolved for the ``llm_vision`` verifier.
        timeout_seconds: Per-evaluation cap on the verifier call.
        colour_tolerance: Default normalised RGB distance tolerance the
            heuristic applies when an expectation omits its own.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = False
    verifier_kind: VisionVerifierKind = VisionVerifierKind.NOOP
    model_tier: ModelTier = "medium"
    timeout_seconds: float = Field(
        default=VISION_TIMEOUT_DEFAULT_SECONDS,
        gt=0.0,
        le=VISION_TIMEOUT_MAX_SECONDS,
    )
    colour_tolerance: float = Field(
        default=VISION_DEFAULT_COLOUR_TOLERANCE,
        ge=0.0,
        le=1.0,
    )


class McpSelfConsumerConfig(BaseModel):
    """Agent -> SynthOrg-MCP self-consumer bridge configuration.

    Attributes:
        mode: Bridge dispatch mode (default ``DISABLED``: no bridge).
        elevated_capabilities: Capability patterns granted to an agent
            whose earned trust level is ``ELEVATED`` (default ``("*",)``
            -- the full MCP surface, still gated behind ELEVATED trust
            and the per-handler admin guardrails).
        read_tool_allowlist: Explicit MCP tool names a sub-ELEVATED
            agent may call. Empty (default) means a low-trust agent
            gets no MCP access -- the safest posture; operators opt in
            by naming tools. An explicit allowlist sidesteps the
            ``*:read`` heuristic, whose pattern would miss
            ``list``/``get``/``status`` actions.
        denied_tools: MCP tool names always excluded regardless of
            trust level or allowlist (highest-priority denylist).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    mode: McpSelfConsumerMode = McpSelfConsumerMode.DISABLED
    elevated_capabilities: tuple[NotBlankStr, ...] = ("*",)
    read_tool_allowlist: tuple[NotBlankStr, ...] = ()
    denied_tools: tuple[NotBlankStr, ...] = ()


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

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="enabled",
            namespace=SettingNamespace.SECURITY,
            key="enabled",
            parse=parse_bool,
        ),
        MirrorField(
            field="audit_enabled",
            namespace=SettingNamespace.SECURITY,
            key="audit_enabled",
            parse=parse_bool,
        ),
        MirrorField(
            field="post_tool_scanning_enabled",
            namespace=SettingNamespace.SECURITY,
            key="post_tool_scanning_enabled",
            parse=parse_bool,
        ),
        MirrorField(
            field="output_scan_policy_type",
            namespace=SettingNamespace.SECURITY,
            key="output_scan_policy_type",
        ),
        MirrorField(
            field="audit_retention_days",
            namespace=SettingNamespace.SECURITY,
            key="audit_retention_days",
            parse=parse_int,
        ),
        MirrorField(
            field="audit_retention_loop_enabled",
            namespace=SettingNamespace.SECURITY,
            key="audit_retention_loop_enabled",
            parse=parse_bool,
        ),
        MirrorField(
            field="audit_retention_tick_seconds",
            namespace=SettingNamespace.SECURITY,
            key="audit_retention_tick_seconds",
            parse=parse_float,
        ),
    )

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
    mcp_self_consumer: McpSelfConsumerConfig = Field(
        default_factory=McpSelfConsumerConfig,
        description="Agent -> SynthOrg-MCP self-consumer bridge config",
    )
    red_team: RedTeamConfig = Field(
        default_factory=RedTeamConfig,
        description=("Adversarial red-team gate config (off by default; opt-in)."),
    )
    vision_verify: VisionVerifyConfig = Field(
        default_factory=VisionVerifyConfig,
        description=("Vision verifier gate config (off by default; opt-in)."),
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
    audit_retention_loop_enabled: bool = Field(
        default=True,
        description=(
            "Live kill-switch for the audit retention purge loop."
            " When ``False`` the loop stays resident but every tick"
            " short-circuits -- used during incident investigations"
            " to preserve all records."
        ),
    )
    audit_retention_tick_seconds: float = Field(
        default=AUDIT_RETENTION_TICK_DEFAULT_SECONDS,
        ge=AUDIT_RETENTION_TICK_MIN_SECONDS,
        le=AUDIT_RETENTION_TICK_MAX_SECONDS,
        description=(
            "Wall-clock interval between audit retention purge ticks. Default 24h."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: Any) -> Any:
        """Overlay setting-namespace mirrors onto the raw input.

        Returns:
            The input data with mirrored settings applied.
        """
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)

    @model_validator(mode="after")
    def _check_disjoint_action_types(self) -> SecurityConfig:
        """Reject overlapping hard-deny and auto-approve action types.

        Returns:
            The validated config.

        Raises:
            ValueError: If an action type is both hard-denied and
                auto-approved.
        """
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
        """Reject duplicate custom policy names.

        Returns:
            The validated config.

        Raises:
            ValueError: If two custom policies share a name.
        """
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

        Returns:
            The validated config.

        Raises:
            ValueError: If any enabled custom policy yields ALLOW or
                ESCALATE while bypass mode is enabled.
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
