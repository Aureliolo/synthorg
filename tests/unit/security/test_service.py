"""Tests for the SecOps service."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.enums import (
    ApprovalRiskLevel,
    ApprovalStatus,
    AutonomyLevel,
    ToolCategory,
)
from synthorg.security.audit import AuditLog
from synthorg.security.autonomy.models import EffectiveAutonomy
from synthorg.security.config import SecurityConfig
from synthorg.security.models import (
    OutputScanResult,
    ScanOutcome,
    SecurityContext,
    SecurityVerdict,
    SecurityVerdictType,
)
from synthorg.security.output_scan_policy import LogOnlyPolicy, WithholdPolicy
from synthorg.security.output_scanner import OutputScanner
from synthorg.security.rules.engine import RuleEngine
from synthorg.security.service import SecOpsService

if TYPE_CHECKING:
    from synthorg.security.output_scan_policy import OutputScanResponsePolicy

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
            outcome=ScanOutcome.REDACTED,
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
            outcome=ScanOutcome.REDACTED,
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


# ── Tests: autonomy pre-check ────────────────────────────────────


@pytest.mark.unit
class TestAutonomyPrecheck:
    """Autonomy-based action routing before the rule engine."""

    def _make_service_with_autonomy(
        self,
        *,
        effective_autonomy: EffectiveAutonomy | None = None,
        config: SecurityConfig | None = None,
        engine_verdict: SecurityVerdict | None = None,
        approval_store: AsyncMock | None = None,
    ) -> SecOpsService:
        """Construct a SecOpsService with autonomy support."""
        cfg = config or SecurityConfig()
        rule_engine = MagicMock(spec=RuleEngine)
        rule_engine.evaluate.return_value = engine_verdict or _make_allow_verdict()
        audit_log = AuditLog()
        output_scanner = MagicMock(spec=OutputScanner)
        output_scanner.scan.return_value = OutputScanResult()

        service = SecOpsService(
            config=cfg,
            rule_engine=rule_engine,
            audit_log=audit_log,
            output_scanner=output_scanner,
            approval_store=approval_store,
            effective_autonomy=effective_autonomy,
        )
        service._test_rule_engine = rule_engine  # type: ignore[attr-defined]
        service._test_audit_log = audit_log  # type: ignore[attr-defined]
        return service

    async def test_auto_approve_keeps_allow(self) -> None:
        """When rule engine ALLOWs and action is auto-approved, stays ALLOW."""
        autonomy = EffectiveAutonomy(
            level=AutonomyLevel.SEMI,
            auto_approve_actions=frozenset({"code:read"}),
            human_approval_actions=frozenset({"infra:deploy"}),
            security_agent=False,
        )
        service = self._make_service_with_autonomy(effective_autonomy=autonomy)
        ctx = _make_context(action_type="code:read")

        verdict = await service.evaluate_pre_tool(ctx)

        assert verdict.verdict == SecurityVerdictType.ALLOW
        # Rule engine always runs first — even for auto-approved actions.
        service._test_rule_engine.evaluate.assert_called_once()  # type: ignore[attr-defined]

    async def test_human_approval_escalates_with_store(self) -> None:
        """Human-approval action with store → ESCALATE after rule engine ALLOW."""
        autonomy = EffectiveAutonomy(
            level=AutonomyLevel.SEMI,
            auto_approve_actions=frozenset({"code:read"}),
            human_approval_actions=frozenset({"infra:deploy"}),
            security_agent=False,
        )
        store = AsyncMock()
        store.add = AsyncMock()
        service = self._make_service_with_autonomy(
            effective_autonomy=autonomy,
            approval_store=store,
        )
        ctx = _make_context(action_type="infra:deploy")

        verdict = await service.evaluate_pre_tool(ctx)

        assert verdict.verdict == SecurityVerdictType.ESCALATE
        assert verdict.approval_id is not None
        store.add.assert_called_once()
        # Rule engine always runs first.
        service._test_rule_engine.evaluate.assert_called_once()  # type: ignore[attr-defined]

    async def test_human_approval_without_store_becomes_deny(self) -> None:
        """Human-approval action without store → DENY."""
        autonomy = EffectiveAutonomy(
            level=AutonomyLevel.SEMI,
            auto_approve_actions=frozenset({"code:read"}),
            human_approval_actions=frozenset({"infra:deploy"}),
            security_agent=False,
        )
        service = self._make_service_with_autonomy(
            effective_autonomy=autonomy,
            approval_store=None,
        )
        ctx = _make_context(action_type="infra:deploy")

        verdict = await service.evaluate_pre_tool(ctx)

        assert verdict.verdict == SecurityVerdictType.DENY
        assert "escalation unavailable" in verdict.reason.lower()
        # Rule engine always runs first — even when escalation fails.
        service._test_rule_engine.evaluate.assert_called_once()  # type: ignore[attr-defined]

    async def test_rule_engine_deny_overrides_auto_approve(self) -> None:
        """Rule engine DENY takes precedence over autonomy auto-approve."""
        autonomy = EffectiveAutonomy(
            level=AutonomyLevel.SEMI,
            auto_approve_actions=frozenset({"deploy:production"}),
            human_approval_actions=frozenset(),
            security_agent=False,
        )
        deny_verdict = _make_deny_verdict()
        service = self._make_service_with_autonomy(
            effective_autonomy=autonomy,
            engine_verdict=deny_verdict,
        )
        ctx = _make_context(action_type="deploy:production")

        verdict = await service.evaluate_pre_tool(ctx)

        # Security detectors take precedence over autonomy.
        assert verdict.verdict == SecurityVerdictType.DENY
        service._test_rule_engine.evaluate.assert_called_once()  # type: ignore[attr-defined]

    async def test_unknown_action_falls_through(self) -> None:
        """When action is not in any autonomy set, rule engine verdict used."""
        autonomy = EffectiveAutonomy(
            level=AutonomyLevel.SEMI,
            auto_approve_actions=frozenset({"code:read"}),
            human_approval_actions=frozenset({"infra:deploy"}),
            security_agent=False,
        )
        allow_verdict = _make_allow_verdict()
        service = self._make_service_with_autonomy(
            effective_autonomy=autonomy,
            engine_verdict=allow_verdict,
        )
        ctx = _make_context(action_type="test:run")

        verdict = await service.evaluate_pre_tool(ctx)

        assert verdict.verdict == SecurityVerdictType.ALLOW
        service._test_rule_engine.evaluate.assert_called_once()  # type: ignore[attr-defined]

    async def test_no_autonomy_uses_rule_engine(self) -> None:
        """When effective_autonomy=None, rule engine is used normally."""
        allow_verdict = _make_allow_verdict()
        service = self._make_service_with_autonomy(
            effective_autonomy=None,
            engine_verdict=allow_verdict,
        )
        ctx = _make_context(action_type="code:read")

        verdict = await service.evaluate_pre_tool(ctx)

        assert verdict.verdict == SecurityVerdictType.ALLOW
        service._test_rule_engine.evaluate.assert_called_once()  # type: ignore[attr-defined]


# ── Tests: scan_output audit failure (Gap 6) ─────────────────────


@pytest.mark.unit
class TestSecOpsScanOutputAuditFailure:
    """When audit recording fails during scan_output, result is still returned."""

    @staticmethod
    def _make_service_with_failing_audit(
        scan_result: OutputScanResult,
    ) -> SecOpsService:
        """Build a service whose audit_log.record raises."""
        cfg = SecurityConfig()
        rule_engine = MagicMock(spec=RuleEngine)
        rule_engine.evaluate.return_value = _make_allow_verdict()
        audit_log = MagicMock(spec=AuditLog)
        audit_log.record.side_effect = RuntimeError("disk full")
        output_scanner = MagicMock(spec=OutputScanner)
        output_scanner.scan.return_value = scan_result
        return SecOpsService(
            config=cfg,
            rule_engine=rule_engine,
            audit_log=audit_log,
            output_scanner=output_scanner,
        )

    async def test_scan_audit_failure_still_returns_result(self) -> None:
        """Scan result is returned even when audit recording fails."""
        finding = OutputScanResult(
            has_sensitive_data=True,
            findings=("API key detected",),
            redacted_content="[REDACTED]",
            outcome=ScanOutcome.REDACTED,
        )
        service = self._make_service_with_failing_audit(finding)
        ctx = _make_context()

        result = await service.scan_output(ctx, "AKIAIOSFODNN7EXAMPLE")

        assert result.has_sensitive_data is True
        assert result.redacted_content == "[REDACTED]"

    async def test_scan_audit_failure_does_not_propagate(self) -> None:
        """RuntimeError from audit_log.record does not propagate."""
        finding = OutputScanResult(
            has_sensitive_data=True,
            findings=("secret",),
            redacted_content="[REDACTED]",
            outcome=ScanOutcome.REDACTED,
        )
        service = self._make_service_with_failing_audit(finding)
        ctx = _make_context()

        # Should not raise.
        result = await service.scan_output(ctx, "secret data")
        assert result is not None


# ── Tests: scan_output policy integration ─────────────────────────


@pytest.mark.unit
class TestSecOpsScanOutputPolicy:
    """Output scan policy is applied after scanning."""

    @staticmethod
    def _make_service_with_policy(
        *,
        scan_result: OutputScanResult,
        policy: OutputScanResponsePolicy | None = None,
    ) -> SecOpsService:
        cfg = SecurityConfig()
        rule_engine = MagicMock(spec=RuleEngine)
        rule_engine.evaluate.return_value = _make_allow_verdict()
        audit_log = AuditLog()
        output_scanner = MagicMock(spec=OutputScanner)
        output_scanner.scan.return_value = scan_result
        service = SecOpsService(
            config=cfg,
            rule_engine=rule_engine,
            audit_log=audit_log,
            output_scanner=output_scanner,
            output_scan_policy=policy,
        )
        service._test_audit_log = audit_log  # type: ignore[attr-defined]
        return service

    async def test_policy_applied_after_scan(self) -> None:
        """Policy's apply method is called with the scan result."""
        mock_policy = MagicMock()
        mock_policy.apply.return_value = OutputScanResult()
        finding = OutputScanResult(
            has_sensitive_data=True,
            findings=("token",),
            redacted_content="[REDACTED]",
            outcome=ScanOutcome.REDACTED,
        )
        service = self._make_service_with_policy(
            scan_result=finding,
            policy=mock_policy,
        )
        ctx = _make_context()

        await service.scan_output(ctx, "Bearer eyJ...")

        mock_policy.apply.assert_called_once()
        call_args = mock_policy.apply.call_args[0]
        assert call_args[0] == finding  # scan result
        assert call_args[1] == ctx  # context

    async def test_policy_transforms_result(self) -> None:
        """The result from policy.apply is what's returned."""
        finding = OutputScanResult(
            has_sensitive_data=True,
            findings=("key",),
            redacted_content="redacted output",
            outcome=ScanOutcome.REDACTED,
        )
        service = self._make_service_with_policy(
            scan_result=finding,
            policy=WithholdPolicy(),
        )
        ctx = _make_context()

        result = await service.scan_output(ctx, "API_KEY=abc123")

        # WithholdPolicy clears redacted_content.
        assert result.has_sensitive_data is True
        assert result.redacted_content is None

    async def test_policy_called_on_clean_result(self) -> None:
        """Policy.apply is called even when scan finds no sensitive data."""
        mock_policy = MagicMock()
        mock_policy.apply.return_value = OutputScanResult()
        clean = OutputScanResult()
        service = self._make_service_with_policy(
            scan_result=clean,
            policy=mock_policy,
        )
        ctx = _make_context()

        await service.scan_output(ctx, "clean output")

        mock_policy.apply.assert_called_once()
        call_args = mock_policy.apply.call_args[0]
        assert call_args[0] == clean
        assert call_args[0].has_sensitive_data is False

    async def test_default_policy_from_config_passes_through(self) -> None:
        """Default config uses RedactPolicy, which passes result through."""
        finding = OutputScanResult(
            has_sensitive_data=True,
            findings=("secret",),
            redacted_content="[REDACTED]",
            outcome=ScanOutcome.REDACTED,
        )
        service = self._make_service_with_policy(
            scan_result=finding,
            policy=None,
        )
        ctx = _make_context()

        result = await service.scan_output(ctx, "secret data")

        # Default config → RedactPolicy → identity transform.
        assert result == finding

    async def test_policy_failure_returns_raw_scan_result(self) -> None:
        """When policy.apply raises, raw scan result is returned."""
        failing_policy = MagicMock()
        failing_policy.name = "broken"
        failing_policy.apply.side_effect = RuntimeError("policy bug")
        finding = OutputScanResult(
            has_sensitive_data=True,
            findings=("token",),
            redacted_content="[REDACTED]",
            outcome=ScanOutcome.REDACTED,
        )
        service = self._make_service_with_policy(
            scan_result=finding,
            policy=failing_policy,
        )
        ctx = _make_context()

        # Should NOT raise — returns raw scan result.
        result = await service.scan_output(ctx, "Bearer eyJ...")

        assert result == finding

    @pytest.mark.parametrize(
        ("exc_cls", "exc_msg"),
        [
            (MemoryError, "oom"),
            (RecursionError, "max depth"),
        ],
    )
    async def test_non_recoverable_policy_errors_propagate(
        self,
        exc_cls: type[BaseException],
        exc_msg: str,
    ) -> None:
        """MemoryError/RecursionError from policy.apply propagate."""
        failing_policy = MagicMock()
        failing_policy.name = "bad-policy"
        failing_policy.apply.side_effect = exc_cls(exc_msg)
        finding = OutputScanResult(
            has_sensitive_data=True,
            findings=("key",),
            redacted_content="[REDACTED]",
            outcome=ScanOutcome.REDACTED,
        )
        service = self._make_service_with_policy(
            scan_result=finding,
            policy=failing_policy,
        )
        ctx = _make_context()

        with pytest.raises(exc_cls):
            await service.scan_output(ctx, "secret")

    async def test_audit_preserves_findings_before_policy_clears_them(
        self,
    ) -> None:
        """Audit entry records original findings even when policy clears."""
        finding = OutputScanResult(
            has_sensitive_data=True,
            findings=("Bearer token",),
            redacted_content="[REDACTED]",
            outcome=ScanOutcome.REDACTED,
        )
        service = self._make_service_with_policy(
            scan_result=finding,
            policy=LogOnlyPolicy(),
        )
        ctx = _make_context()

        result = await service.scan_output(ctx, "Bearer eyJ...")

        # Policy clears findings in the returned result.
        assert result.has_sensitive_data is False
        assert result.findings == ()

        # But the audit entry (recorded before policy) has the originals.
        audit_log = service._test_audit_log  # type: ignore[attr-defined]
        assert audit_log.count() == 1
        entry = audit_log.entries[0]
        assert "Bearer token" in entry.reason
