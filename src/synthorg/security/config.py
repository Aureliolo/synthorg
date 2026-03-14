"""Security configuration models.

Defines ``SecurityConfig`` (the top-level security configuration),
``RuleEngineConfig``, ``SecurityPolicyRule``, and
``OutputScanPolicyType`` for output scan response policy selection.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import ActionType, ApprovalRiskLevel
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.security.models import SecurityVerdictType


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
            (default — falls back to ``REDACT`` when no autonomy
            is configured).
    """

    REDACT = "redact"
    WITHHOLD = "withhold"
    LOG_ONLY = "log_only"
    AUTONOMY_TIERED = "autonomy_tiered"


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

    model_config = ConfigDict(frozen=True)

    name: NotBlankStr
    description: str = ""
    action_types: tuple[str, ...] = ()
    verdict: SecurityVerdictType = SecurityVerdictType.DENY
    risk_level: ApprovalRiskLevel = ApprovalRiskLevel.MEDIUM
    enabled: bool = True

    @model_validator(mode="after")
    def _check_action_type_format(self) -> SecurityPolicyRule:
        """Validate that action_types entries use ``category:action`` format."""
        for at in self.action_types:
            if ":" not in at:
                msg = (
                    f"action_type {at!r} in policy {self.name!r} must use "
                    "'category:action' format"
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
    """

    model_config = ConfigDict(frozen=True)

    credential_patterns_enabled: bool = True
    data_leak_detection_enabled: bool = True
    destructive_op_detection_enabled: bool = True
    path_traversal_detection_enabled: bool = True
    max_argument_length: int = Field(default=100_000, gt=0)


class SecurityConfig(BaseModel):
    """Top-level security configuration.

    Attributes:
        enabled: Master switch for the security subsystem.
        rule_engine: Rule engine configuration.
        audit_enabled: Whether to record audit entries.
        post_tool_scanning_enabled: Scan tool output for secrets.
        hard_deny_action_types: Action types always denied.
        auto_approve_action_types: Action types always approved.
        output_scan_policy_type: Output scan response policy
            (default: ``AUTONOMY_TIERED``).
        custom_policies: User-defined policy rules.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    rule_engine: RuleEngineConfig = Field(
        default_factory=RuleEngineConfig,
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
