"""Tests for the risk classifier."""

import pytest

from synthorg.core.enums import ActionType, ApprovalRiskLevel
from synthorg.security.rules.risk_classifier import RiskClassifier

pytestmark = pytest.mark.timeout(30)


# ── CRITICAL risk level ──────────────────────────────────────────────


@pytest.mark.unit
class TestRiskClassifierCritical:
    """Action types classified as CRITICAL risk."""

    @pytest.mark.parametrize(
        "action_type",
        [
            ActionType.DEPLOY_PRODUCTION,
            ActionType.DB_ADMIN,
            ActionType.ORG_FIRE,
        ],
    )
    def test_critical_risk(self, action_type: str) -> None:
        classifier = RiskClassifier()
        assert classifier.classify(action_type) == ApprovalRiskLevel.CRITICAL


# ── HIGH risk level ──────────────────────────────────────────────────


@pytest.mark.unit
class TestRiskClassifierHigh:
    """Action types classified as HIGH risk."""

    @pytest.mark.parametrize(
        "action_type",
        [
            ActionType.DEPLOY_STAGING,
            ActionType.DB_MUTATE,
            ActionType.CODE_DELETE,
            ActionType.VCS_PUSH,
            ActionType.COMMS_EXTERNAL,
            ActionType.BUDGET_EXCEED,
        ],
    )
    def test_high_risk(self, action_type: str) -> None:
        classifier = RiskClassifier()
        assert classifier.classify(action_type) == ApprovalRiskLevel.HIGH


# ── MEDIUM risk level ────────────────────────────────────────────────


@pytest.mark.unit
class TestRiskClassifierMedium:
    """Action types classified as MEDIUM risk."""

    @pytest.mark.parametrize(
        "action_type",
        [
            ActionType.CODE_CREATE,
            ActionType.CODE_WRITE,
            ActionType.CODE_REFACTOR,
            ActionType.VCS_COMMIT,
            ActionType.ARCH_DECIDE,
            ActionType.ORG_HIRE,
            ActionType.ORG_PROMOTE,
            ActionType.BUDGET_SPEND,
        ],
    )
    def test_medium_risk(self, action_type: str) -> None:
        classifier = RiskClassifier()
        assert classifier.classify(action_type) == ApprovalRiskLevel.MEDIUM


# ── LOW risk level ───────────────────────────────────────────────────


@pytest.mark.unit
class TestRiskClassifierLow:
    """Action types classified as LOW risk."""

    @pytest.mark.parametrize(
        "action_type",
        [
            ActionType.CODE_READ,
            ActionType.TEST_RUN,
            ActionType.TEST_WRITE,
            ActionType.DOCS_WRITE,
            ActionType.VCS_BRANCH,
            ActionType.VCS_READ,
            ActionType.COMMS_INTERNAL,
            ActionType.DB_QUERY,
        ],
    )
    def test_low_risk(self, action_type: str) -> None:
        classifier = RiskClassifier()
        assert classifier.classify(action_type) == ApprovalRiskLevel.LOW


# ── Unknown action types default to HIGH ─────────────────────────────


@pytest.mark.unit
class TestRiskClassifierUnknownDefaults:
    """Unknown action types fall back to HIGH risk (fail-safe per D19)."""

    @pytest.mark.parametrize(
        "action_type",
        [
            "custom:action",
            "unknown:operation",
            "foo:bar",
            "",
        ],
        ids=[
            "custom_action",
            "unknown_operation",
            "arbitrary_string",
            "empty_string",
        ],
    )
    def test_unknown_defaults_to_high(
        self,
        action_type: str,
    ) -> None:
        classifier = RiskClassifier()
        assert classifier.classify(action_type) == ApprovalRiskLevel.HIGH


# ── Custom risk map overrides ────────────────────────────────────────


@pytest.mark.unit
class TestRiskClassifierCustomMap:
    """Custom risk map merges with and overrides defaults."""

    def test_custom_override_existing(self) -> None:
        """Custom map can override a built-in mapping."""
        classifier = RiskClassifier(
            custom_risk_map={
                ActionType.CODE_READ: ApprovalRiskLevel.HIGH,
            },
        )
        assert classifier.classify(ActionType.CODE_READ) == ApprovalRiskLevel.HIGH

    def test_custom_adds_new_action_type(self) -> None:
        """Custom map can add new action types not in defaults."""
        classifier = RiskClassifier(
            custom_risk_map={
                "custom:special": ApprovalRiskLevel.CRITICAL,
            },
        )
        assert classifier.classify("custom:special") == ApprovalRiskLevel.CRITICAL

    def test_custom_map_preserves_unaffected_defaults(self) -> None:
        """Custom map only affects specified keys; others remain."""
        classifier = RiskClassifier(
            custom_risk_map={
                "custom:new": ApprovalRiskLevel.LOW,
            },
        )
        # Existing defaults should be unchanged.
        assert (
            classifier.classify(ActionType.DEPLOY_PRODUCTION)
            == ApprovalRiskLevel.CRITICAL
        )
        assert classifier.classify(ActionType.CODE_READ) == ApprovalRiskLevel.LOW

    def test_none_custom_map_uses_defaults(self) -> None:
        """Passing None for custom_risk_map uses defaults only."""
        classifier = RiskClassifier(custom_risk_map=None)
        assert classifier.classify(ActionType.CODE_READ) == ApprovalRiskLevel.LOW


# ── All ActionType members are mapped ────────────────────────────────


@pytest.mark.unit
class TestRiskClassifierCompleteness:
    """Every ActionType member has a risk mapping in the default map."""

    def test_all_action_types_are_mapped(self) -> None:
        """Every ActionType enum member has an explicit risk mapping."""
        classifier = RiskClassifier()
        for action_type in ActionType:
            # Verify the action type is in the explicit map
            # (not just falling back to HIGH).
            assert action_type.value in classifier._risk_map, (
                f"{action_type} missing from explicit risk map"
            )
            risk = classifier.classify(action_type)
            assert isinstance(risk, ApprovalRiskLevel), (
                f"{action_type} returned invalid risk level"
            )
