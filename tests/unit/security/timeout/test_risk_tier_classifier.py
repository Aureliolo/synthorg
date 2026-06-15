"""Tests for the tiered-timeout default risk classifier binding."""

from collections.abc import Mapping

import pytest

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.observability.events.timeout import TIMEOUT_UNKNOWN_ACTION_TYPE
from synthorg.security.autonomy.enums import ActionType
from synthorg.security.risk_map import (
    MapBackedRiskClassifier,
    default_risk_classifier,
)


def _default_classifier(
    custom_map: Mapping[str, ApprovalRiskLevel] | None = None,
) -> MapBackedRiskClassifier:
    """Build the timeout default classifier under test.

    Returns:
        A ``MapBackedRiskClassifier`` over the default map with the
        timeout-side miss event.
    """
    return default_risk_classifier(
        miss_event=TIMEOUT_UNKNOWN_ACTION_TYPE,
        custom_map=custom_map,
    )


class TestDefaultMapping:
    """Default risk tier mapping."""

    @pytest.mark.unit
    def test_critical_actions(self) -> None:
        classifier = _default_classifier()
        expected = ApprovalRiskLevel.CRITICAL
        assert classifier.classify(ActionType.DEPLOY_PRODUCTION) == expected
        assert classifier.classify(ActionType.DB_ADMIN) == expected

    @pytest.mark.unit
    def test_high_actions(self) -> None:
        classifier = _default_classifier()
        assert classifier.classify(ActionType.VCS_PUSH) == ApprovalRiskLevel.HIGH
        assert classifier.classify(ActionType.CODE_DELETE) == ApprovalRiskLevel.HIGH

    @pytest.mark.unit
    def test_medium_actions(self) -> None:
        classifier = _default_classifier()
        assert classifier.classify(ActionType.CODE_WRITE) == ApprovalRiskLevel.MEDIUM

    @pytest.mark.unit
    def test_low_actions(self) -> None:
        classifier = _default_classifier()
        assert classifier.classify(ActionType.CODE_READ) == ApprovalRiskLevel.LOW
        assert classifier.classify(ActionType.TEST_RUN) == ApprovalRiskLevel.LOW
        assert classifier.classify(ActionType.MEMORY_READ) == ApprovalRiskLevel.LOW


class TestUnknownFallback:
    """Unknown action types default to HIGH (D19)."""

    @pytest.mark.unit
    def test_unknown_defaults_to_high(self) -> None:
        classifier = _default_classifier()
        assert classifier.classify("unknown:action") == ApprovalRiskLevel.HIGH


class TestCustomMap:
    """Custom risk overrides."""

    @pytest.mark.unit
    def test_custom_override(self) -> None:
        classifier = _default_classifier(
            custom_map={ActionType.CODE_READ: ApprovalRiskLevel.CRITICAL}
        )
        assert classifier.classify(ActionType.CODE_READ) == ApprovalRiskLevel.CRITICAL

    @pytest.mark.unit
    def test_custom_preserves_defaults(self) -> None:
        classifier = _default_classifier(
            custom_map={"custom:action": ApprovalRiskLevel.LOW}
        )
        # Default still works.
        assert classifier.classify(ActionType.CODE_READ) == ApprovalRiskLevel.LOW
        # Custom also works.
        assert classifier.classify("custom:action") == ApprovalRiskLevel.LOW
