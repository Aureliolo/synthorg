"""Tests for HumanOnlyPromotionStrategy."""

import pytest

from synthorg.core.enums import AutonomyLevel, DowngradeReason
from synthorg.security.autonomy.change_strategy import HumanOnlyPromotionStrategy


class TestPromotion:
    """Promotion is always denied in human-only strategy."""

    @pytest.mark.unit
    def test_promotion_denied(self) -> None:
        strategy = HumanOnlyPromotionStrategy()
        result = strategy.request_promotion("agent-1", AutonomyLevel.FULL)
        assert result is False

    @pytest.mark.unit
    @pytest.mark.parametrize("target", list(AutonomyLevel))
    def test_all_promotions_denied(self, target: AutonomyLevel) -> None:
        strategy = HumanOnlyPromotionStrategy()
        assert strategy.request_promotion("agent-x", target) is False


class TestAutoDowngrade:
    """Auto-downgrade maps reasons to specific levels."""

    @pytest.mark.unit
    def test_high_error_rate_to_supervised(self) -> None:
        strategy = HumanOnlyPromotionStrategy()
        result = strategy.auto_downgrade("agent-1", DowngradeReason.HIGH_ERROR_RATE)
        assert result == AutonomyLevel.SUPERVISED

    @pytest.mark.unit
    def test_budget_exhausted_to_supervised(self) -> None:
        strategy = HumanOnlyPromotionStrategy()
        result = strategy.auto_downgrade("agent-1", DowngradeReason.BUDGET_EXHAUSTED)
        assert result == AutonomyLevel.SUPERVISED

    @pytest.mark.unit
    def test_security_incident_to_locked(self) -> None:
        strategy = HumanOnlyPromotionStrategy()
        result = strategy.auto_downgrade("agent-1", DowngradeReason.SECURITY_INCIDENT)
        assert result == AutonomyLevel.LOCKED

    @pytest.mark.unit
    def test_override_tracked(self) -> None:
        strategy = HumanOnlyPromotionStrategy()
        strategy.auto_downgrade("agent-1", DowngradeReason.HIGH_ERROR_RATE)
        override = strategy.get_override("agent-1")
        assert override is not None
        assert override.current_level == AutonomyLevel.SUPERVISED
        assert override.reason == DowngradeReason.HIGH_ERROR_RATE
        assert override.requires_human_recovery is True

    @pytest.mark.unit
    def test_no_override_when_not_downgraded(self) -> None:
        strategy = HumanOnlyPromotionStrategy()
        assert strategy.get_override("agent-1") is None

    @pytest.mark.unit
    def test_double_downgrade_preserves_original(self) -> None:
        strategy = HumanOnlyPromotionStrategy()
        strategy.auto_downgrade("agent-1", DowngradeReason.HIGH_ERROR_RATE)
        strategy.auto_downgrade("agent-1", DowngradeReason.SECURITY_INCIDENT)
        override = strategy.get_override("agent-1")
        assert override is not None
        # Second downgrade replaces the first
        assert override.current_level == AutonomyLevel.LOCKED
        assert override.reason == DowngradeReason.SECURITY_INCIDENT
        # Original level is preserved from the FIRST downgrade
        assert override.original_level == AutonomyLevel.SEMI

    @pytest.mark.unit
    def test_downgrade_never_increases_autonomy(self) -> None:
        """LOCKED agent + HIGH_ERROR_RATE should stay LOCKED, not go to SUPERVISED."""
        strategy = HumanOnlyPromotionStrategy()
        strategy.auto_downgrade(
            "agent-1",
            DowngradeReason.SECURITY_INCIDENT,
            current_level=AutonomyLevel.SEMI,
        )
        # Now agent is LOCKED. HIGH_ERROR_RATE targets SUPERVISED — but
        # that's higher than LOCKED, so agent should stay LOCKED.
        result = strategy.auto_downgrade("agent-1", DowngradeReason.HIGH_ERROR_RATE)
        assert result == AutonomyLevel.LOCKED
        override = strategy.get_override("agent-1")
        assert override is not None
        assert override.current_level == AutonomyLevel.LOCKED


class TestRecovery:
    """Recovery is always denied in human-only strategy."""

    @pytest.mark.unit
    def test_recovery_denied(self) -> None:
        strategy = HumanOnlyPromotionStrategy()
        result = strategy.request_recovery("agent-1")
        assert result is False


class TestOverrideManagement:
    """Override clear/get operations."""

    @pytest.mark.unit
    def test_clear_existing_override(self) -> None:
        strategy = HumanOnlyPromotionStrategy()
        strategy.auto_downgrade("agent-1", DowngradeReason.HIGH_ERROR_RATE)
        assert strategy.clear_override("agent-1") is True
        assert strategy.get_override("agent-1") is None

    @pytest.mark.unit
    def test_clear_nonexistent_override(self) -> None:
        strategy = HumanOnlyPromotionStrategy()
        assert strategy.clear_override("agent-1") is False
