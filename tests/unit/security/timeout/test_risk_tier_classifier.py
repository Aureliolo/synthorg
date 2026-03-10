"""Tests for YamlRiskTierClassifier."""

import pytest

from ai_company.core.enums import ActionType, ApprovalRiskLevel
from ai_company.security.timeout.risk_tier_classifier import YamlRiskTierClassifier


class TestDefaultMapping:
    """Default risk tier mapping."""

    @pytest.mark.unit
    def test_critical_actions(self) -> None:
        classifier = YamlRiskTierClassifier()
        expected = ApprovalRiskLevel.CRITICAL
        assert classifier.classify(ActionType.DEPLOY_PRODUCTION) == expected
        assert classifier.classify(ActionType.DB_ADMIN) == expected

    @pytest.mark.unit
    def test_high_actions(self) -> None:
        classifier = YamlRiskTierClassifier()
        assert classifier.classify(ActionType.VCS_PUSH) == ApprovalRiskLevel.HIGH
        assert classifier.classify(ActionType.CODE_DELETE) == ApprovalRiskLevel.HIGH

    @pytest.mark.unit
    def test_medium_actions(self) -> None:
        classifier = YamlRiskTierClassifier()
        assert classifier.classify(ActionType.CODE_WRITE) == ApprovalRiskLevel.MEDIUM

    @pytest.mark.unit
    def test_low_actions(self) -> None:
        classifier = YamlRiskTierClassifier()
        assert classifier.classify(ActionType.CODE_READ) == ApprovalRiskLevel.LOW
        assert classifier.classify(ActionType.TEST_RUN) == ApprovalRiskLevel.LOW


class TestUnknownFallback:
    """Unknown action types default to HIGH (D19)."""

    @pytest.mark.unit
    def test_unknown_defaults_to_high(self) -> None:
        classifier = YamlRiskTierClassifier()
        assert classifier.classify("unknown:action") == ApprovalRiskLevel.HIGH


class TestCustomMap:
    """Custom risk overrides."""

    @pytest.mark.unit
    def test_custom_override(self) -> None:
        classifier = YamlRiskTierClassifier(
            custom_map={ActionType.CODE_READ: ApprovalRiskLevel.CRITICAL}
        )
        assert classifier.classify(ActionType.CODE_READ) == ApprovalRiskLevel.CRITICAL

    @pytest.mark.unit
    def test_custom_preserves_defaults(self) -> None:
        classifier = YamlRiskTierClassifier(
            custom_map={"custom:action": ApprovalRiskLevel.LOW}
        )
        # Default still works.
        assert classifier.classify(ActionType.CODE_READ) == ApprovalRiskLevel.LOW
        # Custom also works.
        assert classifier.classify("custom:action") == ApprovalRiskLevel.LOW
