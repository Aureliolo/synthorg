"""Tests for security domain models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.core.enums import ApprovalRiskLevel, ToolCategory
from synthorg.security.models import (
    AuditEntry,
    OutputScanResult,
    ScanOutcome,
    SecurityContext,
    SecurityVerdict,
    SecurityVerdictType,
)

pytestmark = pytest.mark.timeout(30)

# Shared timezone-aware timestamp for tests.
_NOW = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC)


# ── SecurityVerdictType ──────────────────────────────────────────


@pytest.mark.unit
class TestSecurityVerdictType:
    """Tests for SecurityVerdictType string constants."""

    def test_allow_value(self) -> None:
        assert SecurityVerdictType.ALLOW.value == "allow"

    def test_deny_value(self) -> None:
        assert SecurityVerdictType.DENY.value == "deny"

    def test_escalate_value(self) -> None:
        assert SecurityVerdictType.ESCALATE.value == "escalate"

    def test_constants_are_strings(self) -> None:
        assert isinstance(SecurityVerdictType.ALLOW, str)
        assert isinstance(SecurityVerdictType.DENY, str)
        assert isinstance(SecurityVerdictType.ESCALATE, str)


# ── SecurityVerdict ──────────────────────────────────────────────


@pytest.mark.unit
class TestSecurityVerdict:
    """Tests for SecurityVerdict creation, validation, and immutability."""

    def test_creation_with_valid_data(self) -> None:
        verdict = SecurityVerdict(
            verdict=SecurityVerdictType.ALLOW,
            reason="Tool is safe",
            risk_level=ApprovalRiskLevel.LOW,
            evaluated_at=_NOW,
            evaluation_duration_ms=1.5,
        )
        assert verdict.verdict == "allow"
        assert verdict.reason == "Tool is safe"
        assert verdict.risk_level is ApprovalRiskLevel.LOW
        assert verdict.matched_rules == ()
        assert verdict.evaluated_at == _NOW
        assert verdict.evaluation_duration_ms == 1.5
        assert verdict.approval_id is None

    def test_creation_with_all_fields(self) -> None:
        verdict = SecurityVerdict(
            verdict=SecurityVerdictType.ESCALATE,
            reason="Needs approval",
            risk_level=ApprovalRiskLevel.HIGH,
            matched_rules=("rule-a", "rule-b"),
            evaluated_at=_NOW,
            evaluation_duration_ms=50.0,
            approval_id="apr-123",
        )
        assert verdict.matched_rules == ("rule-a", "rule-b")
        assert verdict.approval_id == "apr-123"

    def test_frozen(self) -> None:
        verdict = SecurityVerdict(
            verdict=SecurityVerdictType.DENY,
            reason="Blocked",
            risk_level=ApprovalRiskLevel.CRITICAL,
            evaluated_at=_NOW,
            evaluation_duration_ms=0.0,
        )
        with pytest.raises(ValidationError):
            verdict.verdict = SecurityVerdictType.ALLOW  # type: ignore[misc]

    def test_blank_reason_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SecurityVerdict(
                verdict=SecurityVerdictType.ALLOW,
                reason="",
                risk_level=ApprovalRiskLevel.LOW,
                evaluated_at=_NOW,
                evaluation_duration_ms=0.0,
            )

    def test_whitespace_only_reason_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace"):
            SecurityVerdict(
                verdict=SecurityVerdictType.ALLOW,
                reason="   ",
                risk_level=ApprovalRiskLevel.LOW,
                evaluated_at=_NOW,
                evaluation_duration_ms=0.0,
            )

    def test_negative_evaluation_duration_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SecurityVerdict(
                verdict=SecurityVerdictType.ALLOW,
                reason="ok",
                risk_level=ApprovalRiskLevel.LOW,
                evaluated_at=_NOW,
                evaluation_duration_ms=-1.0,
            )

    def test_evaluation_duration_zero_accepted(self) -> None:
        verdict = SecurityVerdict(
            verdict=SecurityVerdictType.ALLOW,
            reason="ok",
            risk_level=ApprovalRiskLevel.LOW,
            evaluated_at=_NOW,
            evaluation_duration_ms=0.0,
        )
        assert verdict.evaluation_duration_ms == 0.0

    def test_blank_matched_rule_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SecurityVerdict(
                verdict=SecurityVerdictType.DENY,
                reason="blocked",
                risk_level=ApprovalRiskLevel.HIGH,
                matched_rules=("",),
                evaluated_at=_NOW,
                evaluation_duration_ms=1.0,
            )

    def test_whitespace_only_approval_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace"):
            SecurityVerdict(
                verdict=SecurityVerdictType.ESCALATE,
                reason="needs review",
                risk_level=ApprovalRiskLevel.HIGH,
                evaluated_at=_NOW,
                evaluation_duration_ms=1.0,
                approval_id="   ",
            )

    def test_approval_id_on_deny_rejected(self) -> None:
        """approval_id is only allowed on ESCALATE verdicts."""
        with pytest.raises(ValidationError, match="approval_id"):
            SecurityVerdict(
                verdict=SecurityVerdictType.DENY,
                reason="Blocked",
                risk_level=ApprovalRiskLevel.HIGH,
                evaluated_at=_NOW,
                evaluation_duration_ms=0.0,
                approval_id="apr-invalid",
            )

    def test_approval_id_on_allow_rejected(self) -> None:
        """approval_id is only allowed on ESCALATE verdicts."""
        with pytest.raises(ValidationError, match="approval_id"):
            SecurityVerdict(
                verdict=SecurityVerdictType.ALLOW,
                reason="Safe",
                risk_level=ApprovalRiskLevel.LOW,
                evaluated_at=_NOW,
                evaluation_duration_ms=0.0,
                approval_id="apr-also-invalid",
            )

    def test_json_roundtrip(self) -> None:
        verdict = SecurityVerdict(
            verdict=SecurityVerdictType.ESCALATE,
            reason="Escalated",
            risk_level=ApprovalRiskLevel.HIGH,
            matched_rules=("rule-1",),
            evaluated_at=_NOW,
            evaluation_duration_ms=12.5,
            approval_id="apr-456",
        )
        json_str = verdict.model_dump_json()
        restored = SecurityVerdict.model_validate_json(json_str)
        assert restored == verdict


# ── SecurityContext ───────────────────────────────────────────────


@pytest.mark.unit
class TestSecurityContext:
    """Tests for SecurityContext creation, validation, and immutability."""

    def test_creation_with_required_fields(self) -> None:
        ctx = SecurityContext(
            tool_name="git_commit",
            tool_category=ToolCategory.VERSION_CONTROL,
            action_type="vcs:commit",
        )
        assert ctx.tool_name == "git_commit"
        assert ctx.tool_category is ToolCategory.VERSION_CONTROL
        assert ctx.action_type == "vcs:commit"
        assert ctx.arguments == {}
        assert ctx.agent_id is None
        assert ctx.task_id is None

    def test_creation_with_all_fields(self) -> None:
        ctx = SecurityContext(
            tool_name="file_write",
            tool_category=ToolCategory.FILE_SYSTEM,
            action_type="code:write",
            arguments={"path": "/src/main.py", "content": "print('hi')"},
            agent_id="agent-1",
            task_id="task-42",
        )
        assert ctx.arguments == {
            "path": "/src/main.py",
            "content": "print('hi')",
        }
        assert ctx.agent_id == "agent-1"
        assert ctx.task_id == "task-42"

    def test_frozen(self) -> None:
        ctx = SecurityContext(
            tool_name="git_push",
            tool_category=ToolCategory.VERSION_CONTROL,
            action_type="vcs:push",
        )
        with pytest.raises(ValidationError):
            ctx.tool_name = "other"  # type: ignore[misc]

    def test_blank_tool_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SecurityContext(
                tool_name="",
                tool_category=ToolCategory.FILE_SYSTEM,
                action_type="code:read",
            )

    def test_whitespace_only_tool_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace"):
            SecurityContext(
                tool_name="   ",
                tool_category=ToolCategory.FILE_SYSTEM,
                action_type="code:read",
            )

    def test_blank_agent_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SecurityContext(
                tool_name="read_file",
                tool_category=ToolCategory.FILE_SYSTEM,
                action_type="code:read",
                agent_id="",
            )

    def test_blank_task_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SecurityContext(
                tool_name="read_file",
                tool_category=ToolCategory.FILE_SYSTEM,
                action_type="code:read",
                task_id="",
            )

    def test_action_type_without_colon_rejected(self) -> None:
        """action_type must use 'category:action' format."""
        with pytest.raises(ValidationError, match="category:action"):
            SecurityContext(
                tool_name="some_tool",
                tool_category=ToolCategory.FILE_SYSTEM,
                action_type="nocolon",
            )

    def test_json_roundtrip(self) -> None:
        ctx = SecurityContext(
            tool_name="db_query",
            tool_category=ToolCategory.DATABASE,
            action_type="db:query",
            arguments={"sql": "SELECT 1"},
            agent_id="agent-x",
            task_id="task-99",
        )
        json_str = ctx.model_dump_json()
        restored = SecurityContext.model_validate_json(json_str)
        assert restored == ctx


# ── AuditEntry ───────────────────────────────────────────────────


@pytest.mark.unit
class TestAuditEntry:
    """Tests for AuditEntry creation, validation, and immutability."""

    def test_creation_with_required_fields(self) -> None:
        entry = AuditEntry(
            id="aud-001",
            timestamp=_NOW,
            tool_name="git_push",
            tool_category=ToolCategory.VERSION_CONTROL,
            action_type="vcs:push",
            arguments_hash="a" * 64,
            verdict="allow",
            risk_level=ApprovalRiskLevel.LOW,
            reason="Auto-approved",
            evaluation_duration_ms=5.0,
        )
        assert entry.id == "aud-001"
        assert entry.timestamp == _NOW
        assert entry.agent_id is None
        assert entry.task_id is None
        assert entry.tool_name == "git_push"
        assert entry.tool_category is ToolCategory.VERSION_CONTROL
        assert entry.action_type == "vcs:push"
        assert entry.arguments_hash == "a" * 64
        assert entry.verdict == "allow"
        assert entry.risk_level is ApprovalRiskLevel.LOW
        assert entry.reason == "Auto-approved"
        assert entry.matched_rules == ()
        assert entry.evaluation_duration_ms == 5.0
        assert entry.approval_id is None

    def test_creation_with_all_fields(self) -> None:
        entry = AuditEntry(
            id="aud-002",
            timestamp=_NOW,
            agent_id="agent-a",
            task_id="task-7",
            tool_name="deploy_prod",
            tool_category=ToolCategory.DEPLOYMENT,
            action_type="deploy:production",
            arguments_hash="b" * 64,
            verdict="escalate",
            risk_level=ApprovalRiskLevel.CRITICAL,
            reason="Production deploy escalated",
            matched_rules=("hard-deny-prod",),
            evaluation_duration_ms=0.3,
            approval_id="apr-789",
        )
        assert entry.agent_id == "agent-a"
        assert entry.task_id == "task-7"
        assert entry.matched_rules == ("hard-deny-prod",)
        assert entry.approval_id == "apr-789"

    def test_frozen(self) -> None:
        entry = AuditEntry(
            id="aud-003",
            timestamp=_NOW,
            tool_name="read_file",
            tool_category=ToolCategory.FILE_SYSTEM,
            action_type="code:read",
            arguments_hash="c" * 64,
            verdict="allow",
            risk_level=ApprovalRiskLevel.LOW,
            reason="Read allowed",
            evaluation_duration_ms=0.1,
        )
        with pytest.raises(ValidationError):
            entry.verdict = "deny"  # type: ignore[misc]

    def test_blank_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AuditEntry(
                id="",
                timestamp=_NOW,
                tool_name="test",
                tool_category=ToolCategory.OTHER,
                action_type="test:run",
                arguments_hash="d" * 64,
                verdict="allow",
                risk_level=ApprovalRiskLevel.LOW,
                reason="ok",
                evaluation_duration_ms=0.0,
            )

    def test_blank_tool_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AuditEntry(
                id="aud-x",
                timestamp=_NOW,
                tool_name="",
                tool_category=ToolCategory.OTHER,
                action_type="test:run",
                arguments_hash="d" * 64,
                verdict="allow",
                risk_level=ApprovalRiskLevel.LOW,
                reason="ok",
                evaluation_duration_ms=0.0,
            )

    def test_blank_reason_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AuditEntry(
                id="aud-x",
                timestamp=_NOW,
                tool_name="test",
                tool_category=ToolCategory.OTHER,
                action_type="test:run",
                arguments_hash="d" * 64,
                verdict="allow",
                risk_level=ApprovalRiskLevel.LOW,
                reason="",
                evaluation_duration_ms=0.0,
            )

    def test_negative_evaluation_duration_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AuditEntry(
                id="aud-x",
                timestamp=_NOW,
                tool_name="test",
                tool_category=ToolCategory.OTHER,
                action_type="test:run",
                arguments_hash="d" * 64,
                verdict="allow",
                risk_level=ApprovalRiskLevel.LOW,
                reason="ok",
                evaluation_duration_ms=-0.1,
            )

    def test_json_roundtrip(self) -> None:
        entry = AuditEntry(
            id="aud-rt",
            timestamp=_NOW,
            agent_id="agent-rt",
            task_id="task-rt",
            tool_name="write_file",
            tool_category=ToolCategory.FILE_SYSTEM,
            action_type="code:write",
            arguments_hash="e" * 64,
            verdict="escalate",
            risk_level=ApprovalRiskLevel.MEDIUM,
            reason="Needs review",
            matched_rules=("rule-x",),
            evaluation_duration_ms=2.5,
            approval_id="apr-rt",
        )
        json_str = entry.model_dump_json()
        restored = AuditEntry.model_validate_json(json_str)
        assert restored == entry


# ── OutputScanResult ─────────────────────────────────────────────


@pytest.mark.unit
class TestOutputScanResult:
    """Tests for OutputScanResult defaults, creation, and immutability."""

    def test_defaults(self) -> None:
        result = OutputScanResult()
        assert result.has_sensitive_data is False
        assert result.findings == ()
        assert result.redacted_content is None
        assert result.outcome == ScanOutcome.CLEAN

    def test_with_findings(self) -> None:
        result = OutputScanResult(
            has_sensitive_data=True,
            findings=("API key detected", "Email address found"),
            redacted_content="content with [REDACTED]",
            outcome=ScanOutcome.REDACTED,
        )
        assert result.has_sensitive_data is True
        assert len(result.findings) == 2
        assert result.findings[0] == "API key detected"
        assert result.findings[1] == "Email address found"
        assert result.redacted_content == "content with [REDACTED]"
        assert result.outcome == ScanOutcome.REDACTED

    def test_frozen(self) -> None:
        result = OutputScanResult()
        with pytest.raises(ValidationError):
            result.has_sensitive_data = True  # type: ignore[misc]

    def test_blank_finding_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OutputScanResult(
                has_sensitive_data=True,
                findings=("",),
            )

    def test_whitespace_only_finding_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace"):
            OutputScanResult(
                has_sensitive_data=True,
                findings=("   ",),
            )

    def test_findings_rejected_when_not_sensitive(self) -> None:
        """findings must be empty when has_sensitive_data is False."""
        with pytest.raises(ValidationError, match="findings"):
            OutputScanResult(
                has_sensitive_data=False,
                findings=("unexpected",),
            )

    def test_redacted_content_rejected_when_not_sensitive(self) -> None:
        """redacted_content must be None when has_sensitive_data is False."""
        with pytest.raises(ValidationError, match="redacted_content"):
            OutputScanResult(
                has_sensitive_data=False,
                redacted_content="should not be set",
            )

    def test_json_roundtrip(self) -> None:
        result = OutputScanResult(
            has_sensitive_data=True,
            findings=("PII detected",),
            redacted_content="safe output",
            outcome=ScanOutcome.REDACTED,
        )
        json_str = result.model_dump_json()
        restored = OutputScanResult.model_validate_json(json_str)
        assert restored == result

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            pytest.param(
                {
                    "has_sensitive_data": True,
                    "findings": ("leak",),
                    "outcome": ScanOutcome.CLEAN,
                },
                "outcome",
                id="clean-rejected-when-sensitive",
            ),
            pytest.param(
                {"has_sensitive_data": False, "outcome": ScanOutcome.REDACTED},
                "outcome",
                id="redacted-rejected-when-not-sensitive",
            ),
            pytest.param(
                {"has_sensitive_data": False, "outcome": ScanOutcome.WITHHELD},
                "outcome",
                id="withheld-rejected-when-not-sensitive",
            ),
            pytest.param(
                {
                    "has_sensitive_data": True,
                    "findings": ("leak",),
                    "outcome": ScanOutcome.REDACTED,
                    "redacted_content": None,
                },
                "redacted_content",
                id="redacted-requires-redacted-content",
            ),
            pytest.param(
                {
                    "has_sensitive_data": True,
                    "findings": ("secret",),
                    "outcome": ScanOutcome.LOG_ONLY,
                },
                "outcome",
                id="log-only-rejected-when-sensitive",
            ),
            pytest.param(
                {
                    "has_sensitive_data": True,
                    "findings": (),
                    "outcome": ScanOutcome.WITHHELD,
                },
                "findings",
                id="empty-findings-rejected-when-sensitive",
            ),
            pytest.param(
                {
                    "has_sensitive_data": True,
                    "findings": ("secret",),
                    "outcome": ScanOutcome.WITHHELD,
                    "redacted_content": "should not be set",
                },
                "redacted_content",
                id="withheld-rejects-non-none-redacted-content",
            ),
        ],
    )
    def test_outcome_validation_rejects_invalid(
        self,
        kwargs: dict[str, object],
        match: str,
    ) -> None:
        """Parametrized: invalid outcome/field combinations are rejected."""
        with pytest.raises(ValidationError, match=match):
            OutputScanResult(**kwargs)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("kwargs", "expected_outcome"),
        [
            pytest.param(
                {"has_sensitive_data": False, "outcome": ScanOutcome.LOG_ONLY},
                ScanOutcome.LOG_ONLY,
                id="log-only-accepted-when-not-sensitive",
            ),
            pytest.param(
                {
                    "has_sensitive_data": True,
                    "findings": ("secret",),
                    "outcome": ScanOutcome.WITHHELD,
                    "redacted_content": None,
                },
                ScanOutcome.WITHHELD,
                id="withheld-valid-when-sensitive",
            ),
        ],
    )
    def test_outcome_validation_accepts_valid(
        self,
        kwargs: dict[str, object],
        expected_outcome: ScanOutcome,
    ) -> None:
        """Parametrized: valid outcome/field combinations are accepted."""
        result = OutputScanResult(**kwargs)  # type: ignore[arg-type]
        assert result.outcome == expected_outcome
