"""Tests for the custom policy rule."""

from datetime import UTC, datetime

import pytest

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.security.autonomy.enums import ActionType, ToolCategory
from synthorg.security.config import SecurityPolicyRule
from synthorg.security.models import (
    EvaluationConfidence,
    SecurityContext,
    SecurityVerdictType,
)
from synthorg.security.rules.custom_policy_rule import CustomPolicyRule
from synthorg.security.rules.protocol import SecurityRule

pytestmark = pytest.mark.unit
# -- Helpers ---------------------------------------------------------


def _ctx(
    *,
    action_type: str = ActionType.CODE_WRITE,
    arguments: dict[str, object] | None = None,
) -> SecurityContext:
    """Build a SecurityContext with sensible defaults."""
    return SecurityContext(
        tool_name="test-tool",
        tool_category=ToolCategory.FILE_SYSTEM,
        action_type=action_type,
        arguments=arguments or {},
    )


def _policy(  # noqa: PLR0913
    *,
    name: str = "test-policy",
    action_types: tuple[str, ...] = ("code:write",),
    verdict: SecurityVerdictType = SecurityVerdictType.DENY,
    risk_level: ApprovalRiskLevel = ApprovalRiskLevel.MEDIUM,
    enabled: bool = True,
    description: str = "",
) -> SecurityPolicyRule:
    return SecurityPolicyRule(
        name=name,
        action_types=action_types,
        verdict=verdict,
        risk_level=risk_level,
        enabled=enabled,
        description=description,
    )


# -- Name property ---------------------------------------------------


class TestCustomPolicyRuleName:
    """Rule name uses 'custom_policy:' prefix."""

    def test_name_prefixed(self) -> None:
        rule = CustomPolicyRule(_policy(name="block-deploy"))
        assert rule.name == "custom_policy:block-deploy"

    def test_name_from_policy(self) -> None:
        rule = CustomPolicyRule(_policy(name="my-rule"))
        assert "my-rule" in rule.name


# -- Matching action_types ------------------------------------------


class TestCustomPolicyRuleMatch:
    """Rule matches when context.action_type is in policy.action_types."""

    def test_matching_action_type_returns_verdict(self) -> None:
        rule = CustomPolicyRule(_policy(action_types=("code:write",)))
        ctx = _ctx(action_type="code:write")

        verdict = rule.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.DENY

    def test_non_matching_action_type_returns_none(self) -> None:
        rule = CustomPolicyRule(_policy(action_types=("code:write",)))
        ctx = _ctx(action_type="code:read")

        assert rule.evaluate(ctx) is None

    def test_multiple_action_types_any_match(self) -> None:
        rule = CustomPolicyRule(
            _policy(action_types=("code:write", "vcs:push", "deploy:staging")),
        )

        assert rule.evaluate(_ctx(action_type="vcs:push")) is not None
        assert rule.evaluate(_ctx(action_type="deploy:staging")) is not None
        assert rule.evaluate(_ctx(action_type="code:read")) is None

    def test_empty_action_types_never_matches(self) -> None:
        rule = CustomPolicyRule(_policy(action_types=()))
        ctx = _ctx(action_type="code:write")

        assert rule.evaluate(ctx) is None


# -- Verdict types ---------------------------------------------------


class TestCustomPolicyRuleVerdicts:
    """Configured verdict type is returned on match."""

    @pytest.mark.parametrize(
        "verdict_type",
        [
            SecurityVerdictType.DENY,
            SecurityVerdictType.ALLOW,
            SecurityVerdictType.ESCALATE,
        ],
    )
    def test_verdict_matches_policy(
        self,
        verdict_type: SecurityVerdictType,
    ) -> None:
        rule = CustomPolicyRule(_policy(verdict=verdict_type))
        ctx = _ctx(action_type="code:write")

        verdict = rule.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == verdict_type


# -- Risk level ------------------------------------------------------


class TestCustomPolicyRuleRiskLevel:
    """Configured risk level is carried through to verdict."""

    @pytest.mark.parametrize(
        "risk",
        [
            ApprovalRiskLevel.LOW,
            ApprovalRiskLevel.MEDIUM,
            ApprovalRiskLevel.HIGH,
            ApprovalRiskLevel.CRITICAL,
        ],
    )
    def test_risk_level_matches_policy(
        self,
        risk: ApprovalRiskLevel,
    ) -> None:
        rule = CustomPolicyRule(_policy(risk_level=risk))
        ctx = _ctx(action_type="code:write")

        verdict = rule.evaluate(ctx)

        assert verdict is not None
        assert verdict.risk_level == risk


# -- Disabled policies -----------------------------------------------


class TestCustomPolicyRuleDisabled:
    """Disabled policies never match, even with matching action_type."""

    def test_disabled_returns_none(self) -> None:
        rule = CustomPolicyRule(
            _policy(enabled=False, action_types=("code:write",)),
        )
        ctx = _ctx(action_type="code:write")

        assert rule.evaluate(ctx) is None


# -- Verdict metadata ------------------------------------------------


class TestCustomPolicyRuleVerdictMetadata:
    """Verdict carries correct metadata."""

    def test_matched_rules_contains_rule_name(self) -> None:
        rule = CustomPolicyRule(_policy(name="my-rule"))
        ctx = _ctx(action_type="code:write")

        verdict = rule.evaluate(ctx)

        assert verdict is not None
        assert "custom_policy:my-rule" in verdict.matched_rules

    def test_confidence_is_high(self) -> None:
        rule = CustomPolicyRule(_policy())
        ctx = _ctx(action_type="code:write")

        verdict = rule.evaluate(ctx)

        assert verdict is not None
        assert verdict.confidence == EvaluationConfidence.HIGH

    def test_reason_contains_policy_name(self) -> None:
        rule = CustomPolicyRule(_policy(name="block-deploy"))
        ctx = _ctx(action_type="code:write")

        verdict = rule.evaluate(ctx)

        assert verdict is not None
        assert "block-deploy" in verdict.reason

    def test_reason_contains_action_type(self) -> None:
        rule = CustomPolicyRule(_policy())
        ctx = _ctx(action_type="code:write")

        verdict = rule.evaluate(ctx)

        assert verdict is not None
        assert "code:write" in verdict.reason

    def test_reason_includes_description_when_present(self) -> None:
        rule = CustomPolicyRule(
            _policy(description="Blocks writes to production code"),
        )
        ctx = _ctx(action_type="code:write")

        verdict = rule.evaluate(ctx)

        assert verdict is not None
        assert "Blocks writes to production code" in verdict.reason

    def test_evaluated_at_set(self) -> None:
        before = datetime.now(UTC)
        rule = CustomPolicyRule(_policy())
        ctx = _ctx(action_type="code:write")

        verdict = rule.evaluate(ctx)

        assert verdict is not None
        assert verdict.evaluated_at >= before

    def test_duration_is_zero(self) -> None:
        """Duration is set by the engine, not individual rules."""
        rule = CustomPolicyRule(_policy())
        ctx = _ctx(action_type="code:write")

        verdict = rule.evaluate(ctx)

        assert verdict is not None
        assert verdict.evaluation_duration_ms == 0.0


class TestCustomPolicyRuleProtocol:
    """CustomPolicyRule conforms to the SecurityRule protocol."""

    def test_satisfies_security_rule_protocol(self) -> None:
        rule = CustomPolicyRule(_policy())
        assert isinstance(rule, SecurityRule)
