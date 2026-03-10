"""Tests for timeout policy implementations."""

from datetime import UTC, datetime

import pytest

from ai_company.core.approval import ApprovalItem
from ai_company.core.enums import ApprovalRiskLevel, ApprovalStatus, TimeoutActionType
from ai_company.security.timeout.config import EscalationStep, TierConfig
from ai_company.security.timeout.policies import (
    DenyOnTimeoutPolicy,
    EscalationChainPolicy,
    TieredTimeoutPolicy,
    WaitForeverPolicy,
)
from ai_company.security.timeout.risk_tier_classifier import YamlRiskTierClassifier


def _make_item(
    action_type: str = "code:write",
    risk_level: ApprovalRiskLevel = ApprovalRiskLevel.MEDIUM,
) -> ApprovalItem:
    """Create a minimal pending approval item."""
    return ApprovalItem(
        id="test-approval-1",
        action_type=action_type,
        title="Test approval",
        description="Test description",
        requested_by="agent-1",
        risk_level=risk_level,
        status=ApprovalStatus.PENDING,
        created_at=datetime.now(UTC),
    )


class TestWaitForeverPolicy:
    """WaitForeverPolicy always returns WAIT."""

    @pytest.mark.unit
    async def test_always_waits(self) -> None:
        policy = WaitForeverPolicy()
        item = _make_item()
        result = await policy.determine_action(item, 0.0)
        assert result.action == TimeoutActionType.WAIT

    @pytest.mark.unit
    async def test_waits_after_long_time(self) -> None:
        policy = WaitForeverPolicy()
        item = _make_item()
        result = await policy.determine_action(item, 999999.0)
        assert result.action == TimeoutActionType.WAIT


class TestDenyOnTimeoutPolicy:
    """DenyOnTimeoutPolicy: WAIT before timeout, DENY after."""

    @pytest.mark.unit
    async def test_wait_before_timeout(self) -> None:
        policy = DenyOnTimeoutPolicy(timeout_seconds=3600.0)
        item = _make_item()
        result = await policy.determine_action(item, 1800.0)
        assert result.action == TimeoutActionType.WAIT

    @pytest.mark.unit
    async def test_deny_at_timeout(self) -> None:
        policy = DenyOnTimeoutPolicy(timeout_seconds=3600.0)
        item = _make_item()
        result = await policy.determine_action(item, 3600.0)
        assert result.action == TimeoutActionType.DENY

    @pytest.mark.unit
    async def test_deny_after_timeout(self) -> None:
        policy = DenyOnTimeoutPolicy(timeout_seconds=3600.0)
        item = _make_item()
        result = await policy.determine_action(item, 7200.0)
        assert result.action == TimeoutActionType.DENY


class TestTieredTimeoutPolicy:
    """TieredTimeoutPolicy: per-risk-tier timeout behavior."""

    @pytest.mark.unit
    async def test_wait_within_tier_timeout(self) -> None:
        tiers = {
            "medium": TierConfig(timeout_minutes=60, on_timeout=TimeoutActionType.DENY),
        }
        policy = TieredTimeoutPolicy(
            tiers=tiers,
            classifier=YamlRiskTierClassifier(),
        )
        item = _make_item(action_type="code:write")  # MEDIUM risk
        result = await policy.determine_action(item, 1800.0)  # 30 min
        assert result.action == TimeoutActionType.WAIT

    @pytest.mark.unit
    async def test_deny_after_tier_timeout(self) -> None:
        tiers = {
            "medium": TierConfig(timeout_minutes=60, on_timeout=TimeoutActionType.DENY),
        }
        policy = TieredTimeoutPolicy(
            tiers=tiers,
            classifier=YamlRiskTierClassifier(),
        )
        item = _make_item(action_type="code:write")  # MEDIUM risk
        result = await policy.determine_action(item, 3601.0)  # > 60 min
        assert result.action == TimeoutActionType.DENY

    @pytest.mark.unit
    async def test_approve_on_tier_timeout(self) -> None:
        tiers = {
            "low": TierConfig(
                timeout_minutes=480, on_timeout=TimeoutActionType.APPROVE
            ),
        }
        policy = TieredTimeoutPolicy(
            tiers=tiers,
            classifier=YamlRiskTierClassifier(),
        )
        item = _make_item(action_type="code:read")  # LOW risk
        result = await policy.determine_action(item, 30000.0)  # > 480 min
        assert result.action == TimeoutActionType.APPROVE

    @pytest.mark.unit
    async def test_no_tier_config_waits(self) -> None:
        policy = TieredTimeoutPolicy(
            tiers={},
            classifier=YamlRiskTierClassifier(),
        )
        item = _make_item()
        result = await policy.determine_action(item, 999999.0)
        assert result.action == TimeoutActionType.WAIT


class TestEscalationChainPolicy:
    """EscalationChainPolicy: chain of escalation steps."""

    @pytest.mark.unit
    async def test_first_step_escalation(self) -> None:
        chain = (
            EscalationStep(role="lead", timeout_minutes=30),
            EscalationStep(role="director", timeout_minutes=60),
        )
        policy = EscalationChainPolicy(
            chain=chain,
            on_chain_exhausted=TimeoutActionType.DENY,
        )
        item = _make_item()
        result = await policy.determine_action(item, 600.0)  # 10 min
        assert result.action == TimeoutActionType.ESCALATE
        assert result.escalate_to == "lead"

    @pytest.mark.unit
    async def test_second_step_escalation(self) -> None:
        chain = (
            EscalationStep(role="lead", timeout_minutes=30),
            EscalationStep(role="director", timeout_minutes=60),
        )
        policy = EscalationChainPolicy(
            chain=chain,
            on_chain_exhausted=TimeoutActionType.DENY,
        )
        item = _make_item()
        # 40 min = past first step (30min), within second (30+60=90min)
        result = await policy.determine_action(item, 2400.0)
        assert result.action == TimeoutActionType.ESCALATE
        assert result.escalate_to == "director"

    @pytest.mark.unit
    async def test_chain_exhausted(self) -> None:
        chain = (
            EscalationStep(role="lead", timeout_minutes=30),
            EscalationStep(role="director", timeout_minutes=60),
        )
        policy = EscalationChainPolicy(
            chain=chain,
            on_chain_exhausted=TimeoutActionType.DENY,
        )
        item = _make_item()
        # 100 min = past both steps (30+60=90min)
        result = await policy.determine_action(item, 6000.0)
        assert result.action == TimeoutActionType.DENY

    @pytest.mark.unit
    async def test_empty_chain_exhausted_immediately(self) -> None:
        policy = EscalationChainPolicy(
            chain=(),
            on_chain_exhausted=TimeoutActionType.DENY,
        )
        item = _make_item()
        result = await policy.determine_action(item, 0.0)
        assert result.action == TimeoutActionType.DENY
