"""Tests for timeout policy implementations."""

from datetime import UTC, datetime

import pytest

from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import ApprovalRiskLevel, ApprovalStatus, TimeoutActionType
from synthorg.security.timeout.config import EscalationStep, TierConfig
from synthorg.security.timeout.policies import (
    DenyOnTimeoutPolicy,
    EscalationChainPolicy,
    TieredTimeoutPolicy,
    WaitForeverPolicy,
)
from synthorg.security.timeout.risk_tier_classifier import DefaultRiskTierClassifier


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

    @pytest.mark.unit
    async def test_negative_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout_seconds must be positive"):
            DenyOnTimeoutPolicy(timeout_seconds=-1.0)

    @pytest.mark.unit
    async def test_zero_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout_seconds must be positive"):
            DenyOnTimeoutPolicy(timeout_seconds=0.0)


class TestTieredTimeoutPolicy:
    """TieredTimeoutPolicy: per-risk-tier timeout behavior."""

    @pytest.mark.unit
    async def test_wait_within_tier_timeout(self) -> None:
        tiers = {
            "medium": TierConfig(timeout_minutes=60, on_timeout=TimeoutActionType.DENY),
        }
        policy = TieredTimeoutPolicy(
            tiers=tiers,
            classifier=DefaultRiskTierClassifier(),
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
            classifier=DefaultRiskTierClassifier(),
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
            classifier=DefaultRiskTierClassifier(),
        )
        item = _make_item(action_type="code:read")  # LOW risk
        result = await policy.determine_action(item, 30000.0)  # > 480 min
        assert result.action == TimeoutActionType.APPROVE

    @pytest.mark.unit
    async def test_no_tier_config_waits(self) -> None:
        policy = TieredTimeoutPolicy(
            tiers={},
            classifier=DefaultRiskTierClassifier(),
        )
        item = _make_item()
        result = await policy.determine_action(item, 999999.0)
        assert result.action == TimeoutActionType.WAIT

    @pytest.mark.unit
    async def test_high_risk_auto_approve_blocked(self) -> None:
        """HIGH risk tier with on_timeout=APPROVE should be overridden to DENY."""
        tiers = {
            "high": TierConfig(
                timeout_minutes=60, on_timeout=TimeoutActionType.APPROVE
            ),
        }
        policy = TieredTimeoutPolicy(
            tiers=tiers,
            classifier=DefaultRiskTierClassifier(),
        )
        item = _make_item(action_type="deploy:staging")  # HIGH risk
        result = await policy.determine_action(item, 3601.0)
        assert result.action == TimeoutActionType.DENY

    @pytest.mark.unit
    async def test_critical_risk_auto_approve_blocked(self) -> None:
        """CRITICAL risk tier with on_timeout=APPROVE should be overridden to DENY."""
        tiers = {
            "critical": TierConfig(
                timeout_minutes=60, on_timeout=TimeoutActionType.APPROVE
            ),
        }
        policy = TieredTimeoutPolicy(
            tiers=tiers,
            classifier=DefaultRiskTierClassifier(),
        )
        item = _make_item(action_type="deploy:production")  # CRITICAL risk
        result = await policy.determine_action(item, 3601.0)
        assert result.action == TimeoutActionType.DENY

    @pytest.mark.unit
    async def test_action_type_based_tier_lookup(self) -> None:
        """TierConfig.actions tuple overrides risk-level-based lookup."""
        tiers = {
            "low": TierConfig(
                timeout_minutes=10,
                on_timeout=TimeoutActionType.APPROVE,
                actions=("deploy:staging",),  # normally HIGH
            ),
        }
        policy = TieredTimeoutPolicy(
            tiers=tiers,
            classifier=DefaultRiskTierClassifier(),
        )
        item = _make_item(action_type="deploy:staging")
        result = await policy.determine_action(item, 601.0)  # > 10 min
        # Despite deploy:staging being HIGH risk, the actions tuple
        # places it in the LOW tier, so APPROVE is allowed.
        assert result.action == TimeoutActionType.APPROVE


class TestEscalationChainPolicy:
    """EscalationChainPolicy: chain of escalation steps."""

    @pytest.mark.unit
    async def test_first_step_waits(self) -> None:
        chain = (
            EscalationStep(role="lead", timeout_minutes=30),
            EscalationStep(role="director", timeout_minutes=60),
        )
        policy = EscalationChainPolicy(
            chain=chain,
            on_chain_exhausted=TimeoutActionType.DENY,
        )
        item = _make_item()
        # 10 min — still within first step, should WAIT (not ESCALATE)
        result = await policy.determine_action(item, 600.0)
        assert result.action == TimeoutActionType.WAIT

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
    async def test_chain_exhausted_approve(self) -> None:
        chain = (EscalationStep(role="lead", timeout_minutes=30),)
        policy = EscalationChainPolicy(
            chain=chain,
            on_chain_exhausted=TimeoutActionType.APPROVE,
        )
        item = _make_item()
        result = await policy.determine_action(item, 3600.0)
        assert result.action == TimeoutActionType.APPROVE

    @pytest.mark.unit
    async def test_empty_chain_exhausted_immediately(self) -> None:
        # Bypass config validation to test policy behavior directly.
        policy = EscalationChainPolicy(
            chain=(),
            on_chain_exhausted=TimeoutActionType.DENY,
        )
        item = _make_item()
        result = await policy.determine_action(item, 0.0)
        assert result.action == TimeoutActionType.DENY
