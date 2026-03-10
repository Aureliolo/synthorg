"""Tests for the SecOps service."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_company.core.enums import ApprovalRiskLevel, ApprovalStatus, ToolCategory
from ai_company.security.audit import AuditLog
from ai_company.security.config import SecurityConfig
from ai_company.security.models import (
    OutputScanResult,
    SecurityContext,
    SecurityVerdict,
    SecurityVerdictType,
)
from ai_company.security.output_scanner import OutputScanner
from ai_company.security.rules.engine import RuleEngine
from ai_company.security.service import SecOpsService

pytestmark = pytest.mark.timeout(30)


# ── Helpers ───────────────────────────────────────────────────────


def _make_context(
    *,
    tool_name: str = "test-tool",
    action_type: str = "code:read",
    agent_id: str | None = "agent-1",
    task_id: str | None = "task-1",
) -> SecurityContext:
    return SecurityContext(
        tool_name=tool_name,
        tool_category=ToolCategory.FILE_SYSTEM,
        action_type=action_type,
        arguments={"path": "/workspace/test"},
        agent_id=agent_id,
        task_id=task_id,
    )


def _make_allow_verdict() -> SecurityVerdict:
    return SecurityVerdict(
        verdict=SecurityVerdictType.ALLOW,
        reason="Allowed by policy",
        risk_level=ApprovalRiskLevel.LOW,
        evaluated_at=datetime.now(UTC),
        evaluation_duration_ms=0.5,
    )


def _make_deny_verdict() -> SecurityVerdict:
    return SecurityVerdict(
        verdict=SecurityVerdictType.DENY,
        reason="Denied by policy",
        risk_level=ApprovalRiskLevel.HIGH,
        matched_rules=("deny-rule",),
        evaluated_at=datetime.now(UTC),
        evaluation_duration_ms=0.5,
    )


def _make_escalate_verdict() -> SecurityVerdict:
    return SecurityVerdict(
        verdict=SecurityVerdictType.ESCALATE,
        reason="Needs human review",
        risk_level=ApprovalRiskLevel.CRITICAL,
        matched_rules=("escalate-rule",),
        evaluated_at=datetime.now(UTC),
        evaluation_duration_ms=0.5,
    )


def _make_service(
    *,
    config: SecurityConfig | None = None,
    engine_verdict: SecurityVerdict | None = None,
    approval_store: AsyncMock | None = None,
    scan_result: OutputScanResult | None = None,
) -> SecOpsService:
    cfg = config or SecurityConfig()
    rule_engine = MagicMock(spec=RuleEngine)
    rule_engine.evaluate.return_value = engine_verdict or _make_allow_verdict()
    audit_log = AuditLog()
    output_scanner = MagicMock(spec=OutputScanner)
    output_scanner.scan.return_value = scan_result or OutputScanResult()

    service = SecOpsService(
        config=cfg,
        rule_engine=rule_engine,
        audit_log=audit_log,
        output_scanner=output_scanner,
        approval_store=approval_store,
    )
    # Expose internals for assertions in tests.
    service._test_rule_engine = rule_engine  # type: ignore[attr-defined]
    service._test_audit_log = audit_log  # type: ignore[attr-defined]
    service._test_output_scanner = output_scanner  # type: ignore[attr-defined]
    return service


# ── Tests: evaluate_pre_tool ──────────────────────────────────────


@pytest.mark.unit
class TestSecOpsDisabled:
    """When security is disabled, always returns ALLOW."""

    async def test_disabled_returns_allow(self) -> None:
        config = SecurityConfig(enabled=False)
        service = _make_service(config=config)
        ctx = _make_context()

        verdict = await service.evaluate_pre_tool(ctx)

        assert verdict.verdict == SecurityVerdictType.ALLOW
        assert "disabled" in verdict.reason.lower()

    async def test_disabled_skips_rule_engine(self) -> None:
        config = SecurityConfig(enabled=False)
        service = _make_service(config=config)
        ctx = _make_context()

        await service.evaluate_pre_tool(ctx)

        service._test_rule_engine.evaluate.assert_not_called()  # type: ignore[attr-defined]

    async def test_disabled_records_audit_when_audit_enabled(self) -> None:
        config = SecurityConfig(enabled=False)
        service = _make_service(config=config)
        ctx = _make_context()

        await service.evaluate_pre_tool(ctx)

        # Even when security is disabled, audit entries are recorded
        # for auditability (when audit_enabled=True, the default).
        assert service._test_audit_log.count() == 1  # type: ignore[attr-defined]

    async def test_disabled_no_audit_when_audit_disabled(self) -> None:
        config = SecurityConfig(enabled=False, audit_enabled=False)
        service = _make_service(config=config)
        ctx = _make_context()

        await service.evaluate_pre_tool(ctx)

        assert service._test_audit_log.count() == 0  # type: ignore[attr-defined]


@pytest.mark.unit
class TestSecOpsAllow:
    """ALLOW verdict from rule engine."""

    async def test_allow_verdict_returned(self) -> None:
        service = _make_service(engine_verdict=_make_allow_verdict())
        ctx = _make_context()

        verdict = await service.evaluate_pre_tool(ctx)

        assert verdict.verdict == SecurityVerdictType.ALLOW

    async def test_allow_records_audit(self) -> None:
        service = _make_service(engine_verdict=_make_allow_verdict())
        ctx = _make_context()

        await service.evaluate_pre_tool(ctx)

        assert service._test_audit_log.count() == 1  # type: ignore[attr-defined]
        entry = service._test_audit_log.entries[0]  # type: ignore[attr-defined]
        assert entry.verdict == SecurityVerdictType.ALLOW
        assert entry.tool_name == "test-tool"

    async def test_allow_audit_contains_agent_info(self) -> None:
        service = _make_service(engine_verdict=_make_allow_verdict())
        ctx = _make_context(agent_id="agent-x", task_id="task-y")

        await service.evaluate_pre_tool(ctx)

        entry = service._test_audit_log.entries[0]  # type: ignore[attr-defined]
        assert entry.agent_id == "agent-x"
        assert entry.task_id == "task-y"

    async def test_audit_disabled_skips_recording(self) -> None:
        config = SecurityConfig(audit_enabled=False)
        service = _make_service(
            config=config,
            engine_verdict=_make_allow_verdict(),
        )
        ctx = _make_context()

        await service.evaluate_pre_tool(ctx)

        assert service._test_audit_log.count() == 0  # type: ignore[attr-defined]


@pytest.mark.unit
class TestSecOpsDeny:
    """DENY verdict from rule engine."""

    async def test_deny_verdict_returned(self) -> None:
        service = _make_service(engine_verdict=_make_deny_verdict())
        ctx = _make_context()

        verdict = await service.evaluate_pre_tool(ctx)

        assert verdict.verdict == SecurityVerdictType.DENY
        assert "Denied by policy" in verdict.reason

    async def test_deny_records_audit(self) -> None:
        service = _make_service(engine_verdict=_make_deny_verdict())
        ctx = _make_context()

        await service.evaluate_pre_tool(ctx)

        assert service._test_audit_log.count() == 1  # type: ignore[attr-defined]
        entry = service._test_audit_log.entries[0]  # type: ignore[attr-defined]
        assert entry.verdict == SecurityVerdictType.DENY

    async def test_deny_matched_rules_in_audit(self) -> None:
        service = _make_service(engine_verdict=_make_deny_verdict())
        ctx = _make_context()

        await service.evaluate_pre_tool(ctx)

        entry = service._test_audit_log.entries[0]  # type: ignore[attr-defined]
        assert "deny-rule" in entry.matched_rules


@pytest.mark.unit
class TestSecOpsEscalateWithStore:
    """ESCALATE verdict with an approval store creates an ApprovalItem."""

    async def test_escalate_creates_approval_item(self) -> None:
        store = AsyncMock()
        store.add = AsyncMock()
        service = _make_service(
            engine_verdict=_make_escalate_verdict(),
            approval_store=store,
        )
        ctx = _make_context()

        verdict = await service.evaluate_pre_tool(ctx)

        assert verdict.verdict == SecurityVerdictType.ESCALATE
        assert verdict.approval_id is not None
        store.add.assert_called_once()

    async def test_escalate_approval_item_fields(self) -> None:
        store = AsyncMock()
        store.add = AsyncMock()
        service = _make_service(
            engine_verdict=_make_escalate_verdict(),
            approval_store=store,
        )
        ctx = _make_context(agent_id="agent-sec", tool_name="risky-tool")

        await service.evaluate_pre_tool(ctx)

        item = store.add.call_args[0][0]
        assert item.action_type == "code:read"
        assert item.requested_by == "agent-sec"
        assert item.risk_level == ApprovalRiskLevel.CRITICAL
        assert item.status == ApprovalStatus.PENDING
        assert item.metadata["tool_name"] == "risky-tool"

    async def test_escalate_records_audit(self) -> None:
        store = AsyncMock()
        store.add = AsyncMock()
        service = _make_service(
            engine_verdict=_make_escalate_verdict(),
            approval_store=store,
        )
        ctx = _make_context()

        verdict = await service.evaluate_pre_tool(ctx)

        assert service._test_audit_log.count() == 1  # type: ignore[attr-defined]
        entry = service._test_audit_log.entries[0]  # type: ignore[attr-defined]
        assert entry.approval_id == verdict.approval_id


@pytest.mark.unit
class TestSecOpsEscalateWithoutStore:
    """ESCALATE verdict without an approval store converts to DENY."""

    async def test_escalate_no_store_becomes_deny(self) -> None:
        service = _make_service(
            engine_verdict=_make_escalate_verdict(),
            approval_store=None,
        )
        ctx = _make_context()

        verdict = await service.evaluate_pre_tool(ctx)

        assert verdict.verdict == SecurityVerdictType.DENY
        assert "escalation unavailable" in verdict.reason.lower()

    async def test_escalate_no_store_preserves_original_reason(self) -> None:
        service = _make_service(
            engine_verdict=_make_escalate_verdict(),
            approval_store=None,
        )
        ctx = _make_context()

        verdict = await service.evaluate_pre_tool(ctx)

        assert "Needs human review" in verdict.reason

    async def test_escalate_no_store_records_audit_as_deny(self) -> None:
        service = _make_service(
            engine_verdict=_make_escalate_verdict(),
            approval_store=None,
        )
        ctx = _make_context()

        await service.evaluate_pre_tool(ctx)

        entry = service._test_audit_log.entries[0]  # type: ignore[attr-defined]
        assert entry.verdict == SecurityVerdictType.DENY


# ── Tests: scan_output ────────────────────────────────────────────


@pytest.mark.unit
class TestSecOpsScanOutput:
    """Output scanning delegates to OutputScanner."""

    async def test_scan_delegates_to_scanner(self) -> None:
        finding_result = OutputScanResult(
            has_sensitive_data=True,
            findings=("provider access key",),
            redacted_content="[REDACTED]",
        )
        service = _make_service(scan_result=finding_result)
        ctx = _make_context()

        result = await service.scan_output(ctx, "some output")

        service._test_output_scanner.scan.assert_called_once_with("some output")  # type: ignore[attr-defined]
        assert result.has_sensitive_data is True
        assert "provider access key" in result.findings

    async def test_scan_clean_output(self) -> None:
        service = _make_service(scan_result=OutputScanResult())
        ctx = _make_context()

        result = await service.scan_output(ctx, "clean output")

        assert result.has_sensitive_data is False

    async def test_scan_records_audit_on_findings(self) -> None:
        finding_result = OutputScanResult(
            has_sensitive_data=True,
            findings=("Bearer token",),
            redacted_content="[REDACTED]",
        )
        service = _make_service(scan_result=finding_result)
        ctx = _make_context()

        await service.scan_output(ctx, "Bearer eyJ...")

        assert service._test_audit_log.count() == 1  # type: ignore[attr-defined]
        entry = service._test_audit_log.entries[0]  # type: ignore[attr-defined]
        assert entry.verdict == "output_scan"
        assert entry.risk_level == ApprovalRiskLevel.HIGH

    async def test_scan_no_audit_when_clean(self) -> None:
        service = _make_service(scan_result=OutputScanResult())
        ctx = _make_context()

        await service.scan_output(ctx, "clean output")

        assert service._test_audit_log.count() == 0  # type: ignore[attr-defined]


@pytest.mark.unit
class TestSecOpsScanOutputDisabled:
    """When post-tool scanning is disabled, returns empty result."""

    async def test_scanning_disabled_returns_empty(self) -> None:
        config = SecurityConfig(post_tool_scanning_enabled=False)
        service = _make_service(config=config)
        ctx = _make_context()

        result = await service.scan_output(ctx, "secret: AKIAIOSFODNN7EXAMPLE")

        assert result.has_sensitive_data is False
        assert result.findings == ()

    async def test_scanning_disabled_skips_scanner(self) -> None:
        config = SecurityConfig(post_tool_scanning_enabled=False)
        service = _make_service(config=config)
        ctx = _make_context()

        await service.scan_output(ctx, "secret data")

        service._test_output_scanner.scan.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.unit
class TestSecOpsFailClosed:
    """When the rule engine raises, service returns DENY (fail-closed)."""

    async def test_rule_engine_exception_returns_deny(self) -> None:
        service = _make_service()
        service._test_rule_engine.evaluate.side_effect = RuntimeError("boom")  # type: ignore[attr-defined]
        ctx = _make_context()

        verdict = await service.evaluate_pre_tool(ctx)

        assert verdict.verdict == SecurityVerdictType.DENY
        assert verdict.risk_level == ApprovalRiskLevel.CRITICAL
        assert "fail-closed" in verdict.reason.lower()

    async def test_rule_engine_exception_still_records_audit(self) -> None:
        service = _make_service()
        service._test_rule_engine.evaluate.side_effect = RuntimeError("boom")  # type: ignore[attr-defined]
        ctx = _make_context()

        await service.evaluate_pre_tool(ctx)

        assert service._test_audit_log.count() == 1  # type: ignore[attr-defined]
        entry = service._test_audit_log.entries[0]  # type: ignore[attr-defined]
        assert entry.verdict == SecurityVerdictType.DENY


@pytest.mark.unit
class TestSecOpsEscalateStoreFailure:
    """When the approval store raises, escalation converts to DENY."""

    async def test_store_failure_converts_to_deny(self) -> None:
        store = AsyncMock()
        store.add = AsyncMock(side_effect=RuntimeError("store down"))
        service = _make_service(
            engine_verdict=_make_escalate_verdict(),
            approval_store=store,
        )
        ctx = _make_context()

        verdict = await service.evaluate_pre_tool(ctx)

        assert verdict.verdict == SecurityVerdictType.DENY
        assert "store error" in verdict.reason.lower()
