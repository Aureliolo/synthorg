"""Security domain models.

Defines the value objects used by the SecOps service: security
verdicts, evaluation contexts, audit entries, and output scan results.
"""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from synthorg.core.enums import ApprovalRiskLevel, ToolCategory  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001


class ScanOutcome(StrEnum):
    """Outcome of an output scan policy decision.

    Tracks what the scanner/policy *did* with the output so that
    downstream consumers (e.g. ``ToolInvoker``) can distinguish
    intentional withholding from scanner failure.

    Attributes:
        CLEAN: No sensitive data detected (default).
        REDACTED: Sensitive data found, redacted content available.
        WITHHELD: Content intentionally withheld by policy.
        LOG_ONLY: Findings discarded by policy, original content passed
            through.  Always emitted with ``has_sensitive_data=False``
            because the policy resets the result — the audit log
            (written by ``SecOpsService`` before the policy runs) is
            the source of truth for what was actually detected.
    """

    CLEAN = "clean"
    REDACTED = "redacted"
    WITHHELD = "withheld"
    LOG_ONLY = "log_only"


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
        approval_id: Set only when verdict is ``escalate``.
    """

    model_config = ConfigDict(frozen=True)

    verdict: SecurityVerdictType
    reason: NotBlankStr
    risk_level: ApprovalRiskLevel
    matched_rules: tuple[NotBlankStr, ...] = ()
    evaluated_at: AwareDatetime
    evaluation_duration_ms: float = Field(ge=0.0)
    approval_id: NotBlankStr | None = None

    @model_validator(mode="after")
    def _check_approval_id(self) -> SecurityVerdict:
        """Enforce that approval_id is only set on ESCALATE verdicts."""
        if (
            self.verdict != SecurityVerdictType.ESCALATE
            and self.approval_id is not None
        ):
            msg = "approval_id must be None when verdict is not ESCALATE"
            raise ValueError(msg)
        return self


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

    @model_validator(mode="after")
    def _check_action_type_format(self) -> SecurityContext:
        """Validate that action_type uses ``category:action`` format."""
        if ":" not in self.action_type:
            msg = (
                f"action_type {self.action_type!r} must use "
                "'category:action' format (missing ':')"
            )
            raise ValueError(msg)
        return self


_HEX_SHA256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

# Verdict values that appear in audit entries.
AuditVerdictStr = Literal["allow", "deny", "escalate", "output_scan"]

# Audit-only verdict for post-tool output scan entries.
OUTPUT_SCAN_VERDICT: AuditVerdictStr = "output_scan"


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
        arguments_hash: SHA-256 hex digest of serialized arguments.
        verdict: One of ``SecurityVerdictType`` values (allow/deny/
            escalate) for pre-tool evaluations, or ``'output_scan'``
            for post-tool output scan entries.
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
    arguments_hash: _HEX_SHA256
    verdict: AuditVerdictStr
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
        outcome: What the scanner/policy did with the output.
            Allows downstream consumers to distinguish intentional
            withholding from scanner failure.
    """

    model_config = ConfigDict(frozen=True)

    has_sensitive_data: bool = False
    findings: tuple[NotBlankStr, ...] = ()
    redacted_content: str | None = None
    outcome: ScanOutcome = ScanOutcome.CLEAN

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
            if self.outcome in (ScanOutcome.REDACTED, ScanOutcome.WITHHELD):
                msg = (
                    f"outcome={self.outcome.value!r} is invalid when "
                    "has_sensitive_data is False"
                )
                raise ValueError(msg)
        elif self.outcome == ScanOutcome.CLEAN:
            msg = "outcome='clean' is invalid when has_sensitive_data is True"
            raise ValueError(msg)
        elif not self.findings:
            msg = "findings must not be empty when has_sensitive_data is True"
            raise ValueError(msg)
        if self.outcome == ScanOutcome.REDACTED and self.redacted_content is None:
            msg = "redacted_content must not be None when outcome is 'redacted'"
            raise ValueError(msg)
        if self.outcome == ScanOutcome.WITHHELD and self.redacted_content is not None:
            msg = "redacted_content must be None when outcome is 'withheld'"
            raise ValueError(msg)
        if self.outcome == ScanOutcome.LOG_ONLY and self.has_sensitive_data:
            msg = "outcome='log_only' is invalid when has_sensitive_data is True"
            raise ValueError(msg)
        return self
