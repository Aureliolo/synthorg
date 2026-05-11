"""Tests for safety classifier and uncertainty checker integration in SecOpsService."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.enums import ApprovalRiskLevel, ToolCategory
from synthorg.security.config import SafetyClassifierConfig, SecurityConfig
from synthorg.security.denial_tracker import DenialTracker
from synthorg.security.models import (
    SecurityContext,
    SecurityVerdict,
    SecurityVerdictType,
)
from synthorg.security.safety_classifier import (
    PermissionTier,
    SafetyClassification,
    SafetyClassifierResult,
)
from synthorg.security.service import SecOpsService
from synthorg.security.uncertainty import UncertaintyResult
from tests._shared import mock_of

# ── Helpers ───────────────────────────────────────────────────────


def _make_context(
    *,
    tool_name: str = "test-tool",
    action_type: str = "code:write",
) -> SecurityContext:
    return SecurityContext(
        tool_name=tool_name,
        tool_category=ToolCategory.FILE_SYSTEM,
        action_type=action_type,
        arguments={"path": "/workspace/test.py"},
        agent_id="agent-1",
        task_id="task-1",
    )


def _make_escalation_verdict() -> SecurityVerdict:
    return SecurityVerdict(
        verdict=SecurityVerdictType.ESCALATE,
        reason="Human approval required",
        risk_level=ApprovalRiskLevel.HIGH,
        evaluated_at=datetime.now(UTC),
        evaluation_duration_ms=1.0,
    )


def _make_service(
    *,
    safety_classifier: Any = None,
    uncertainty_checker: Any = None,
    denial_tracker: DenialTracker | None = None,
    config: SecurityConfig | None = None,
) -> SecOpsService:
    """Build a SecOpsService with mock dependencies."""
    approval_store = mock_of[ApprovalStoreProtocol](
        add=AsyncMock(),
    )

    cfg = config or SecurityConfig()

    from synthorg.security.audit import AuditLog
    from synthorg.security.output_scanner import OutputScanner
    from synthorg.security.rules.engine import RuleEngine
    from synthorg.security.rules.risk_classifier import RiskClassifier

    real_rule_engine = RuleEngine(
        rules=(),
        risk_classifier=RiskClassifier(),
        config=cfg.rule_engine,
    )

    return SecOpsService(
        config=cfg,
        rule_engine=real_rule_engine,
        audit_log=AuditLog(),
        output_scanner=OutputScanner(),
        approval_store=approval_store,
        safety_classifier=safety_classifier,
        uncertainty_checker=uncertainty_checker,
        denial_tracker=denial_tracker,
    )


# ── Tests: safety classifier integration ──────────────────────────


@pytest.mark.unit
class TestSafetyClassifierIntegration:
    """Safety classifier integration in _handle_escalation."""

    async def test_blocked_auto_rejects(self) -> None:
        """BLOCKED classification returns DENY, no approval item."""
        mock_classifier = AsyncMock()
        mock_classifier.classify = AsyncMock(
            return_value=SafetyClassifierResult(
                classification=SafetyClassification.BLOCKED,
                stripped_description="stripped text",
                reason="Credential theft attempt",
                classification_duration_ms=5.0,
            ),
        )
        mock_classifier.classify_tier = lambda at: PermissionTier.CLASSIFIER_GATED

        service = _make_service(safety_classifier=mock_classifier)
        context = _make_context()
        verdict = _make_escalation_verdict()

        result = await service._handle_escalation(context, verdict)

        assert result.verdict == SecurityVerdictType.DENY
        assert (
            "blocked" in result.reason.lower()
            or "auto-rejected" in result.reason.lower()
        )
        # Approval store should NOT have been called
        service._approval_store.add.assert_not_awaited()  # type: ignore[union-attr]

    async def test_suspicious_enriches_metadata(self) -> None:
        """SUSPICIOUS classification adds metadata to approval item."""
        mock_classifier = AsyncMock()
        mock_classifier.classify = AsyncMock(
            return_value=SafetyClassifierResult(
                classification=SafetyClassification.SUSPICIOUS,
                stripped_description="stripped text here",
                reason="Unusual network pattern",
                classification_duration_ms=5.0,
            ),
        )
        mock_classifier.classify_tier = lambda at: PermissionTier.CLASSIFIER_GATED

        service = _make_service(safety_classifier=mock_classifier)
        context = _make_context()
        verdict = _make_escalation_verdict()

        result = await service._handle_escalation(context, verdict)

        assert result.verdict == SecurityVerdictType.ESCALATE
        assert result.approval_id is not None

        # Check the approval item was stored with metadata
        call_args = service._approval_store.add.call_args  # type: ignore[union-attr]
        item = call_args[0][0]
        assert item.metadata["safety_classification"] == "suspicious"
        assert item.metadata["stripped_description"] == "stripped text here"
        assert item.metadata["safety_reason"] == "Unusual network pattern"

    async def test_safe_classification_normal_flow(self) -> None:
        """SAFE classification proceeds normally."""
        mock_classifier = AsyncMock()
        mock_classifier.classify = AsyncMock(
            return_value=SafetyClassifierResult(
                classification=SafetyClassification.SAFE,
                stripped_description="stripped text",
                reason="Action appears safe",
                classification_duration_ms=3.0,
            ),
        )
        mock_classifier.classify_tier = lambda at: PermissionTier.CLASSIFIER_GATED

        service = _make_service(safety_classifier=mock_classifier)
        context = _make_context()
        verdict = _make_escalation_verdict()

        result = await service._handle_escalation(context, verdict)

        assert result.verdict == SecurityVerdictType.ESCALATE
        assert result.approval_id is not None

        call_args = service._approval_store.add.call_args  # type: ignore[union-attr]
        item = call_args[0][0]
        assert item.metadata["safety_classification"] == "safe"

    async def test_classifier_error_still_creates_approval(self) -> None:
        """Classifier failure still creates approval item (fail-safe)."""
        mock_classifier = AsyncMock()
        mock_classifier.classify = AsyncMock(
            side_effect=RuntimeError("LLM connection failed"),
        )
        mock_classifier.classify_tier = lambda at: PermissionTier.CLASSIFIER_GATED

        service = _make_service(safety_classifier=mock_classifier)
        context = _make_context()
        verdict = _make_escalation_verdict()

        result = await service._handle_escalation(context, verdict)

        # Should still create the approval item despite classifier error
        assert result.verdict == SecurityVerdictType.ESCALATE
        assert result.approval_id is not None
        service._approval_store.add.assert_awaited_once()  # type: ignore[union-attr]


# ── Tests: uncertainty checker integration ────────────────────────


@pytest.mark.unit
class TestUncertaintyCheckerIntegration:
    """Uncertainty checker integration in _handle_escalation."""

    async def test_low_confidence_enriches_metadata(self) -> None:
        """Low confidence score is stored in metadata."""
        mock_checker = AsyncMock()
        mock_checker.check = AsyncMock(
            return_value=UncertaintyResult(
                confidence_score=0.3,
                provider_count=2,
                keyword_overlap=0.2,
                embedding_similarity=0.4,
                reason="Cross-provider check complete",
                check_duration_ms=50.0,
            ),
        )
        # Need a safety classifier to produce stripped_description
        # (uncertainty check only runs when stripped text is available).
        service = _make_service(
            safety_classifier=_make_safe_classifier(),
            uncertainty_checker=mock_checker,
        )
        context = _make_context()
        verdict = _make_escalation_verdict()

        result = await service._handle_escalation(context, verdict)

        assert result.verdict == SecurityVerdictType.ESCALATE
        call_args = service._approval_store.add.call_args  # type: ignore[union-attr]
        item = call_args[0][0]
        assert item.metadata["confidence_score"] == "0.3"
        assert item.metadata["keyword_overlap"] == "0.2"
        assert item.metadata["embedding_similarity"] == "0.4"
        assert item.metadata["uncertainty_provider_count"] == "2"

    async def test_checker_error_still_creates_approval(self) -> None:
        """Checker failure still creates approval item."""
        mock_checker = AsyncMock()
        mock_checker.check = AsyncMock(
            side_effect=RuntimeError("Provider timeout"),
        )

        service = _make_service(
            safety_classifier=_make_safe_classifier(),
            uncertainty_checker=mock_checker,
        )
        context = _make_context()
        verdict = _make_escalation_verdict()

        result = await service._handle_escalation(context, verdict)

        assert result.verdict == SecurityVerdictType.ESCALATE
        assert result.approval_id is not None
        service._approval_store.add.assert_awaited_once()  # type: ignore[union-attr]


# ── Tests: both features together ─────────────────────────────────


@pytest.mark.unit
class TestBothFeatures:
    """Both safety classifier and uncertainty checker active."""

    async def test_both_enrich_metadata(self) -> None:
        """Both features contribute metadata to the approval item."""
        mock_classifier = AsyncMock()
        mock_classifier.classify = AsyncMock(
            return_value=SafetyClassifierResult(
                classification=SafetyClassification.SUSPICIOUS,
                stripped_description="stripped",
                reason="Unusual pattern",
                classification_duration_ms=5.0,
            ),
        )
        mock_classifier.classify_tier = lambda at: PermissionTier.CLASSIFIER_GATED

        mock_checker = AsyncMock()
        mock_checker.check = AsyncMock(
            return_value=UncertaintyResult(
                confidence_score=0.7,
                provider_count=2,
                keyword_overlap=0.6,
                embedding_similarity=0.8,
                reason="Check complete",
                check_duration_ms=30.0,
            ),
        )

        service = _make_service(
            safety_classifier=mock_classifier,
            uncertainty_checker=mock_checker,
        )
        context = _make_context()
        verdict = _make_escalation_verdict()

        result = await service._handle_escalation(context, verdict)

        assert result.verdict == SecurityVerdictType.ESCALATE
        call_args = service._approval_store.add.call_args  # type: ignore[union-attr]
        item = call_args[0][0]
        assert item.metadata["safety_classification"] == "suspicious"
        assert item.metadata["confidence_score"] == "0.7"
        assert item.metadata["stripped_description"] == "stripped"

    async def test_blocked_skips_uncertainty_check(self) -> None:
        """When classifier returns BLOCKED, uncertainty check is skipped."""
        mock_classifier = AsyncMock()
        mock_classifier.classify = AsyncMock(
            return_value=SafetyClassifierResult(
                classification=SafetyClassification.BLOCKED,
                stripped_description="stripped",
                reason="Blocked",
                classification_duration_ms=5.0,
            ),
        )
        mock_classifier.classify_tier = lambda at: PermissionTier.CLASSIFIER_GATED

        mock_checker = AsyncMock()
        mock_checker.check = AsyncMock()

        service = _make_service(
            safety_classifier=mock_classifier,
            uncertainty_checker=mock_checker,
        )
        context = _make_context()
        verdict = _make_escalation_verdict()

        await service._handle_escalation(context, verdict)

        # Uncertainty checker should NOT be called when blocked
        mock_checker.check.assert_not_awaited()

    async def test_no_features_unchanged(self) -> None:
        """With neither feature, behavior is unchanged."""
        service = _make_service()
        context = _make_context()
        verdict = _make_escalation_verdict()

        result = await service._handle_escalation(context, verdict)

        assert result.verdict == SecurityVerdictType.ESCALATE
        assert result.approval_id is not None
        call_args = service._approval_store.add.call_args  # type: ignore[union-attr]
        item = call_args[0][0]
        # Only default metadata keys
        assert "safety_classification" not in item.metadata
        assert "confidence_score" not in item.metadata


@pytest.mark.unit
class TestAutoRejectBlockedDisabled:
    """When auto_reject_blocked=False, BLOCKED proceeds to approval."""

    async def test_blocked_not_auto_rejected(self) -> None:
        """BLOCKED with auto_reject_blocked=False creates approval item."""
        mock_classifier = AsyncMock()
        mock_classifier.classify = AsyncMock(
            return_value=SafetyClassifierResult(
                classification=SafetyClassification.BLOCKED,
                stripped_description="stripped",
                reason="Blocked action",
                classification_duration_ms=5.0,
            ),
        )
        mock_classifier.classify_tier = lambda at: PermissionTier.CLASSIFIER_GATED

        cfg = SecurityConfig(
            safety_classifier=SafetyClassifierConfig(
                enabled=True,
                auto_reject_blocked=False,
            ),
        )
        service = _make_service(
            safety_classifier=mock_classifier,
            config=cfg,
        )
        context = _make_context()
        verdict = _make_escalation_verdict()

        result = await service._handle_escalation(context, verdict)

        # Should NOT auto-reject -- proceeds to create approval item
        assert result.verdict == SecurityVerdictType.ESCALATE
        assert result.approval_id is not None
        service._approval_store.add.assert_awaited_once()  # type: ignore[union-attr]
        call_args = service._approval_store.add.call_args  # type: ignore[union-attr]
        item = call_args[0][0]
        assert item.metadata["safety_classification"] == "blocked"


# ── Tests: denial tracker integration ───────────────────────────


def _make_blocked_classifier() -> AsyncMock:
    """Create a mock classifier that always returns BLOCKED."""
    mock = AsyncMock()
    mock.classify = AsyncMock(
        return_value=SafetyClassifierResult(
            classification=SafetyClassification.BLOCKED,
            stripped_description="stripped",
            reason="Blocked action",
            classification_duration_ms=5.0,
        ),
    )
    mock.classify_tier = lambda action_type: PermissionTier.CLASSIFIER_GATED
    return mock


def _make_safe_classifier() -> AsyncMock:
    """Create a mock classifier that always returns SAFE."""
    mock = AsyncMock()
    mock.classify = AsyncMock(
        return_value=SafetyClassifierResult(
            classification=SafetyClassification.SAFE,
            stripped_description="stripped safe",
            reason="Action appears safe",
            classification_duration_ms=3.0,
        ),
    )
    mock.classify_tier = lambda action_type: PermissionTier.CLASSIFIER_GATED
    return mock


@pytest.mark.unit
class TestDenialTrackerIntegration:
    """Denial tracker integration in _handle_escalation."""

    async def test_blocked_with_tracker_retry(self) -> None:
        """BLOCKED with retries remaining returns DENY with retry reason."""
        tracker = DenialTracker(max_consecutive=3, max_total=20)
        service = _make_service(
            safety_classifier=_make_blocked_classifier(),
            denial_tracker=tracker,
        )
        context = _make_context()
        verdict = _make_escalation_verdict()

        result = await service._handle_escalation(context, verdict)

        assert result.verdict == SecurityVerdictType.DENY
        assert "retry" in result.reason.lower()
        assert "safer approach" in result.reason.lower()

    async def test_blocked_with_tracker_escalate(self) -> None:
        """BLOCKED after max denials proceeds to human approval."""
        tracker = DenialTracker(max_consecutive=2, max_total=20)
        service = _make_service(
            safety_classifier=_make_blocked_classifier(),
            denial_tracker=tracker,
        )
        context = _make_context()
        verdict = _make_escalation_verdict()

        # First denial: RETRY -> DENY
        await service._handle_escalation(context, verdict)
        # Second denial: ESCALATE -> human review (not auto-reject)
        result = await service._handle_escalation(context, verdict)

        assert result.verdict == SecurityVerdictType.ESCALATE
        assert result.approval_id is not None

    async def test_safe_resets_consecutive(self) -> None:
        """SAFE classification resets consecutive denial count."""
        tracker = DenialTracker(max_consecutive=2, max_total=20)

        # First: blocked -> 1 consecutive.
        blocked_service = _make_service(
            safety_classifier=_make_blocked_classifier(),
            denial_tracker=tracker,
        )
        context = _make_context()
        verdict = _make_escalation_verdict()
        await blocked_service._handle_escalation(context, verdict)

        consecutive, _ = tracker.get_counts("agent-1")
        assert consecutive == 1

        # Second: safe -> resets consecutive.
        safe_service = _make_service(
            safety_classifier=_make_safe_classifier(),
            denial_tracker=tracker,
        )
        await safe_service._handle_escalation(context, verdict)

        consecutive, total = tracker.get_counts("agent-1")
        assert consecutive == 0
        assert total == 1

    async def test_total_limit_across_resets(self) -> None:
        """Total limit is reached even across consecutive resets."""
        tracker = DenialTracker(max_consecutive=10, max_total=3)

        blocked_svc = _make_service(
            safety_classifier=_make_blocked_classifier(),
            denial_tracker=tracker,
        )
        safe_svc = _make_service(
            safety_classifier=_make_safe_classifier(),
            denial_tracker=tracker,
        )
        context = _make_context()
        verdict = _make_escalation_verdict()

        # Deny, reset, deny, reset, deny -> total=3 -> ESCALATE.
        await blocked_svc._handle_escalation(context, verdict)
        await safe_svc._handle_escalation(context, verdict)
        await blocked_svc._handle_escalation(context, verdict)
        await safe_svc._handle_escalation(context, verdict)
        result = await blocked_svc._handle_escalation(context, verdict)

        # ESCALATE -> proceeds to human approval (not auto-reject).
        assert result.verdict == SecurityVerdictType.ESCALATE
        assert result.approval_id is not None

    async def test_no_tracker_blocked_immediate_deny(self) -> None:
        """Without denial tracker, BLOCKED is immediate DENY."""
        service = _make_service(
            safety_classifier=_make_blocked_classifier(),
        )
        context = _make_context()
        verdict = _make_escalation_verdict()

        result = await service._handle_escalation(context, verdict)

        assert result.verdict == SecurityVerdictType.DENY
        assert "auto-rejected" in result.reason.lower()


# ── Tests: permission tier integration ──────────────────────────


def _make_safe_tool_classifier() -> AsyncMock:
    """Create a mock classifier that returns SAFE_TOOL tier."""
    mock = AsyncMock()
    mock.classify = AsyncMock()
    mock.classify_tier = lambda action_type: PermissionTier.SAFE_TOOL
    return mock


@pytest.mark.unit
class TestPermissionTierIntegration:
    """Permission tier integration in _run_safety_classifier."""

    async def test_safe_tool_bypasses_classifier(self) -> None:
        """SAFE_TOOL tier bypasses the LLM classifier."""
        classifier = _make_safe_tool_classifier()
        service = _make_service(safety_classifier=classifier)
        context = _make_context(action_type="code:read")
        verdict = _make_escalation_verdict()

        result = await service._handle_escalation(context, verdict)

        # Classifier should NOT have been called.
        classifier.classify.assert_not_awaited()
        # Should proceed to create approval item.
        assert result.verdict == SecurityVerdictType.ESCALATE
        assert result.approval_id is not None

    async def test_classifier_gated_runs_classifier(self) -> None:
        """CLASSIFIER_GATED tier runs the full classifier."""
        classifier = _make_safe_classifier()
        service = _make_service(safety_classifier=classifier)
        context = _make_context(action_type="shell:exec")
        verdict = _make_escalation_verdict()

        result = await service._handle_escalation(context, verdict)

        classifier.classify.assert_awaited_once()
        assert result.verdict == SecurityVerdictType.ESCALATE

    async def test_safe_tool_no_metadata_from_classifier(self) -> None:
        """SAFE_TOOL tier does not populate classifier metadata."""
        classifier = _make_safe_tool_classifier()
        service = _make_service(safety_classifier=classifier)
        context = _make_context(action_type="docs:write")
        verdict = _make_escalation_verdict()

        await service._handle_escalation(context, verdict)

        call_args = service._approval_store.add.call_args  # type: ignore[union-attr]
        item = call_args[0][0]
        assert "safety_classification" not in item.metadata
        assert "stripped_description" not in item.metadata
