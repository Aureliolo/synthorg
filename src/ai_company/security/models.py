"""Security domain models.

Defines the value objects used by the SecOps service: security
verdicts, evaluation contexts, audit entries, and output scan results.
"""

from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_company.core.enums import ApprovalRiskLevel, ToolCategory  # noqa: TC001
from ai_company.core.types import NotBlankStr  # noqa: TC001


class SecurityVerdictType(StrEnum):
    """Security verdict constants.

    Three possible outcomes of a security evaluation: the tool call
    is allowed, denied, or escalated for human approval.
    """

    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"


class SecurityVerdict(BaseModel):
    """Result of a security evaluation.

    Attributes:
        verdict: One of ``allow``, ``deny``, ``escalate``.
        reason: Human-readable explanation.
        risk_level: Assessed risk level for the action.
        matched_rules: Names of rules that triggered.
        evaluated_at: Timestamp of evaluation.
        evaluation_duration_ms: How long the evaluation took.
        approval_id: Set when verdict is ``escalate``.
    """

    model_config = ConfigDict(frozen=True)

    verdict: SecurityVerdictType
    reason: NotBlankStr
    risk_level: ApprovalRiskLevel
    matched_rules: tuple[NotBlankStr, ...] = ()
    evaluated_at: AwareDatetime
    evaluation_duration_ms: float = Field(ge=0.0)
    approval_id: NotBlankStr | None = None


class SecurityContext(BaseModel):
    """Context passed to the security evaluator before tool execution.

    Attributes:
        tool_name: Name of the tool being invoked.
        tool_category: Tool's category for access-level gating.
        action_type: Two-level ``category:action`` type string.
        arguments: Tool call arguments for inspection.
        agent_id: ID of the agent requesting the tool.
        task_id: ID of the task being executed.
    """

    model_config = ConfigDict(frozen=True)

    tool_name: NotBlankStr
    tool_category: ToolCategory
    action_type: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    agent_id: NotBlankStr | None = None
    task_id: NotBlankStr | None = None


class AuditEntry(BaseModel):
    """Immutable record of a security evaluation for the audit log.

    Attributes:
        id: Unique entry identifier.
        timestamp: When the evaluation occurred.
        agent_id: Agent that requested the tool.
        task_id: Task being executed.
        tool_name: Tool that was evaluated.
        tool_category: Tool category.
        action_type: Action type string.
        arguments_hash: SHA-256 of serialized arguments (never raw).
        verdict: Allow / deny / escalate / output_scan.
        risk_level: Assessed risk level.
        reason: Explanation of the verdict.
        matched_rules: Rules that triggered.
        evaluation_duration_ms: Duration of evaluation.
        approval_id: Set when verdict is escalate.
    """

    model_config = ConfigDict(frozen=True)

    id: NotBlankStr
    timestamp: AwareDatetime
    agent_id: NotBlankStr | None = None
    task_id: NotBlankStr | None = None
    tool_name: NotBlankStr
    tool_category: ToolCategory
    action_type: str
    arguments_hash: str
    verdict: str
    risk_level: ApprovalRiskLevel
    reason: NotBlankStr
    matched_rules: tuple[NotBlankStr, ...] = ()
    evaluation_duration_ms: float = Field(ge=0.0)
    approval_id: NotBlankStr | None = None


class OutputScanResult(BaseModel):
    """Result of scanning tool output for sensitive data.

    Attributes:
        has_sensitive_data: Whether sensitive data was detected.
        findings: Descriptions of findings.
        redacted_content: Content with sensitive data replaced, or None.
    """

    model_config = ConfigDict(frozen=True)

    has_sensitive_data: bool = False
    findings: tuple[NotBlankStr, ...] = ()
    redacted_content: str | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> OutputScanResult:
        """Enforce consistency between fields."""
        if not self.has_sensitive_data:
            if self.findings:
                msg = "findings must be empty when has_sensitive_data is False"
                raise ValueError(msg)
            if self.redacted_content is not None:
                msg = "redacted_content must be None when has_sensitive_data is False"
                raise ValueError(msg)
        return self
