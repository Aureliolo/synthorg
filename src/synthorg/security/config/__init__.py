"""Security configuration models.

Defines ``SecurityConfig`` (the top-level security configuration) and
re-exports the enum discriminators (``_enums``) and sub-component configs
(``_components``) that compose it, so ``synthorg.security.config`` remains
the single import surface for the security configuration models.
"""

from typing import ClassVar, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.security.autonomy.enums import ActionType
from synthorg.security.config._components import (
    VISION_DEFAULT_COLOUR_TOLERANCE,
    VISION_TIMEOUT_DEFAULT_SECONDS,
    VISION_TIMEOUT_MAX_SECONDS,
    LlmFallbackConfig,
    McpSelfConsumerConfig,
    RedTeamConfig,
    RuleEngineConfig,
    SafetyClassifierConfig,
    SecurityPolicyRule,
    UncertaintyCheckConfig,
    VisionVerifyConfig,
)
from synthorg.security.config._enums import (
    ArgumentTruncationStrategy,
    LlmFallbackErrorPolicy,
    McpSelfConsumerMode,
    OutputScanPolicyType,
    SecurityEnforcementMode,
    VerdictReasonVisibility,
    VisionVerifierKind,
)
from synthorg.security.models import SecurityVerdictType
from synthorg.security.policy_engine.config import SecurityPolicyConfig
from synthorg.settings.definitions.security import (
    AUDIT_RETENTION_TICK_DEFAULT_SECONDS,
    AUDIT_RETENTION_TICK_MAX_SECONDS,
    AUDIT_RETENTION_TICK_MIN_SECONDS,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    MirrorField,
    apply_settings_mirrors,
    parse_bool,
    parse_float,
    parse_int,
)

__all__ = [
    "VISION_DEFAULT_COLOUR_TOLERANCE",
    "VISION_TIMEOUT_DEFAULT_SECONDS",
    "VISION_TIMEOUT_MAX_SECONDS",
    "ArgumentTruncationStrategy",
    "LlmFallbackConfig",
    "LlmFallbackErrorPolicy",
    "McpSelfConsumerConfig",
    "McpSelfConsumerMode",
    "OutputScanPolicyType",
    "RedTeamConfig",
    "RuleEngineConfig",
    "SafetyClassifierConfig",
    "SecurityConfig",
    "SecurityEnforcementMode",
    "SecurityPolicyRule",
    "UncertaintyCheckConfig",
    "VerdictReasonVisibility",
    "VisionVerifierKind",
    "VisionVerifyConfig",
]


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
    rule_engine: RuleEngineConfig = Field(default_factory=RuleEngineConfig)
    llm_fallback: LlmFallbackConfig = Field(default_factory=LlmFallbackConfig)
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
    def _apply_mirrors(cls, data: object) -> object:
        """Overlay setting-namespace mirrors onto the raw input.

        Returns:
            The input data with mirrored settings applied.
        """
        return cast("object", apply_settings_mirrors(data, cls._MIRROR_FIELDS))

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
