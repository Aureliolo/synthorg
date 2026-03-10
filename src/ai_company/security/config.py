"""Security configuration models.

Defines ``SecurityConfig`` (the top-level security configuration),
``RuleEngineConfig``, and ``SecurityPolicyRule`` for custom policies.
"""

from pydantic import BaseModel, ConfigDict, Field

from ai_company.core.enums import ApprovalRiskLevel
from ai_company.core.types import NotBlankStr  # noqa: TC001
from ai_company.security.models import SecurityVerdictType


class SecurityPolicyRule(BaseModel):
    """A single configurable security policy rule.

    Attributes:
        name: Rule name (used in matched_rules lists).
        description: Human-readable description.
        action_types: Action types this rule applies to.
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


class RuleEngineConfig(BaseModel):
    """Configuration for the synchronous rule engine.

    Attributes:
        credential_patterns_enabled: Detect credentials in arguments.
        data_leak_detection_enabled: Detect sensitive file paths / PII.
        destructive_op_detection_enabled: Detect destructive operations.
        path_traversal_detection_enabled: Detect path traversal attacks.
        max_argument_length: Reserved for future use — not yet enforced.
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
        "deploy:production",
        "db:admin",
        "org:fire",
    )
    auto_approve_action_types: tuple[str, ...] = (
        "code:read",
        "docs:write",
    )
    custom_policies: tuple[SecurityPolicyRule, ...] = ()
