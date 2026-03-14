"""Tests for the security rule engine."""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import ActionType, ApprovalRiskLevel, ToolCategory
from synthorg.security.config import RuleEngineConfig
from synthorg.security.models import (
    SecurityContext,
    SecurityVerdict,
    SecurityVerdictType,
)
from synthorg.security.rules.engine import RuleEngine
from synthorg.security.rules.risk_classifier import RiskClassifier

pytestmark = pytest.mark.timeout(30)


# ── Helpers ───────────────────────────────────────────────────────


class _StubRule:
    """Stub rule that returns a fixed verdict or None."""

    def __init__(self, name: str, verdict: SecurityVerdict | None) -> None:
        self._name = name
        self._verdict = verdict
        self.called = False

    @property
    def name(self) -> str:
        return self._name

    def evaluate(self, context: SecurityContext) -> SecurityVerdict | None:
        self.called = True
        return self._verdict


def _make_context(
    *,
    action_type: str = ActionType.CODE_READ,
    tool_name: str = "test-tool",
) -> SecurityContext:
    return SecurityContext(
        tool_name=tool_name,
        tool_category=ToolCategory.FILE_SYSTEM,
        action_type=action_type,
    )


def _make_deny_verdict(reason: str = "Denied by rule") -> SecurityVerdict:
    return SecurityVerdict(
        verdict=SecurityVerdictType.DENY,
        reason=reason,
        risk_level=ApprovalRiskLevel.HIGH,
        matched_rules=("deny-rule",),
        evaluated_at=datetime.now(UTC),
        evaluation_duration_ms=0.0,
    )


def _make_escalate_verdict(
    reason: str = "Escalated by rule",
) -> SecurityVerdict:
    return SecurityVerdict(
        verdict=SecurityVerdictType.ESCALATE,
        reason=reason,
        risk_level=ApprovalRiskLevel.CRITICAL,
        matched_rules=("escalate-rule",),
        evaluated_at=datetime.now(UTC),
        evaluation_duration_ms=0.0,
    )


def _make_engine(
    rules: tuple[_StubRule, ...] = (),
    *,
    risk_classifier: RiskClassifier | None = None,
) -> RuleEngine:
    return RuleEngine(
        rules=rules,
        risk_classifier=risk_classifier or RiskClassifier(),
        config=RuleEngineConfig(),
    )


# ── Tests ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestRuleEngineEmptyRules:
    """When no rules are configured, engine returns ALLOW."""

    def test_empty_rules_returns_allow(self) -> None:
        engine = _make_engine(rules=())
        ctx = _make_context()

        verdict = engine.evaluate(ctx)

        assert verdict.verdict == SecurityVerdictType.ALLOW
        assert verdict.risk_level == ApprovalRiskLevel.LOW

    def test_empty_rules_sets_duration(self) -> None:
        engine = _make_engine(rules=())
        ctx = _make_context()

        verdict = engine.evaluate(ctx)

        assert verdict.evaluation_duration_ms >= 0.0


@pytest.mark.unit
class TestRuleEngineNoMatch:
    """When no rule matches, engine returns ALLOW with risk from classifier."""

    def test_passthrough_rules_return_allow(self) -> None:
        rule_a = _StubRule("pass-a", verdict=None)
        rule_b = _StubRule("pass-b", verdict=None)
        engine = _make_engine(rules=(rule_a, rule_b))
        ctx = _make_context(action_type=ActionType.CODE_READ)

        verdict = engine.evaluate(ctx)

        assert verdict.verdict == SecurityVerdictType.ALLOW
        assert rule_a.called
        assert rule_b.called

    @pytest.mark.parametrize(
        ("action_type", "expected_risk"),
        [
            (ActionType.CODE_READ, ApprovalRiskLevel.LOW),
            (ActionType.CODE_WRITE, ApprovalRiskLevel.MEDIUM),
            (ActionType.DB_MUTATE, ApprovalRiskLevel.HIGH),
            (ActionType.DEPLOY_PRODUCTION, ApprovalRiskLevel.CRITICAL),
        ],
    )
    def test_risk_from_classifier(
        self,
        action_type: str,
        expected_risk: ApprovalRiskLevel,
    ) -> None:
        engine = _make_engine(rules=())
        ctx = _make_context(action_type=action_type)

        verdict = engine.evaluate(ctx)

        assert verdict.risk_level == expected_risk

    def test_unknown_action_type_defaults_to_high(self) -> None:
        engine = _make_engine(rules=())
        ctx = _make_context(action_type="custom:unknown")

        verdict = engine.evaluate(ctx)

        assert verdict.risk_level == ApprovalRiskLevel.HIGH

    def test_reason_indicates_no_rule_triggered(self) -> None:
        engine = _make_engine(rules=())
        ctx = _make_context()

        verdict = engine.evaluate(ctx)

        assert "No security rule triggered" in verdict.reason


@pytest.mark.unit
class TestRuleEngineDeny:
    """First DENY rule wins — subsequent rules are not evaluated."""

    def test_first_deny_wins(self) -> None:
        deny_rule = _StubRule("deny-first", verdict=_make_deny_verdict("first"))
        second_rule = _StubRule("second", verdict=None)
        engine = _make_engine(rules=(deny_rule, second_rule))
        ctx = _make_context()

        verdict = engine.evaluate(ctx)

        assert verdict.verdict == SecurityVerdictType.DENY
        assert "first" in verdict.reason
        assert deny_rule.called
        assert not second_rule.called

    def test_deny_after_passthrough(self) -> None:
        pass_rule = _StubRule("pass", verdict=None)
        deny_rule = _StubRule("deny", verdict=_make_deny_verdict("blocked"))
        later_rule = _StubRule("later", verdict=None)
        engine = _make_engine(rules=(pass_rule, deny_rule, later_rule))
        ctx = _make_context()

        verdict = engine.evaluate(ctx)

        assert verdict.verdict == SecurityVerdictType.DENY
        assert pass_rule.called
        assert deny_rule.called
        assert not later_rule.called

    def test_deny_sets_duration(self) -> None:
        deny_rule = _StubRule("deny", verdict=_make_deny_verdict())
        engine = _make_engine(rules=(deny_rule,))
        ctx = _make_context()

        verdict = engine.evaluate(ctx)

        assert verdict.evaluation_duration_ms >= 0.0


@pytest.mark.unit
class TestRuleEngineEscalate:
    """First ESCALATE rule wins — subsequent rules are not evaluated."""

    def test_first_escalate_wins(self) -> None:
        escalate = _StubRule(
            "escalate-first",
            verdict=_make_escalate_verdict("needs review"),
        )
        second = _StubRule("second", verdict=_make_deny_verdict())
        engine = _make_engine(rules=(escalate, second))
        ctx = _make_context()

        verdict = engine.evaluate(ctx)

        assert verdict.verdict == SecurityVerdictType.ESCALATE
        assert "needs review" in verdict.reason
        assert escalate.called
        assert not second.called

    def test_escalate_after_passthrough(self) -> None:
        pass_rule = _StubRule("pass", verdict=None)
        escalate = _StubRule("esc", verdict=_make_escalate_verdict())
        engine = _make_engine(rules=(pass_rule, escalate))
        ctx = _make_context()

        verdict = engine.evaluate(ctx)

        assert verdict.verdict == SecurityVerdictType.ESCALATE
        assert pass_rule.called
        assert escalate.called


@pytest.mark.unit
class TestRuleEngineDuration:
    """Evaluation duration is always set."""

    def test_duration_set_on_allow(self) -> None:
        engine = _make_engine(rules=())
        ctx = _make_context()

        verdict = engine.evaluate(ctx)

        assert isinstance(verdict.evaluation_duration_ms, float)
        assert verdict.evaluation_duration_ms >= 0.0

    def test_duration_set_on_match(self) -> None:
        deny_rule = _StubRule("deny", verdict=_make_deny_verdict())
        engine = _make_engine(rules=(deny_rule,))
        ctx = _make_context()

        verdict = engine.evaluate(ctx)

        assert isinstance(verdict.evaluation_duration_ms, float)
        assert verdict.evaluation_duration_ms >= 0.0


@pytest.mark.unit
class TestRuleEngineFailClosed:
    """When a rule raises an exception, engine returns DENY (fail-closed)."""

    def test_exception_in_rule_returns_deny(self) -> None:
        class _ExplodingRule:
            @property
            def name(self) -> str:
                return "exploding"

            def evaluate(
                self,
                context: SecurityContext,
            ) -> SecurityVerdict | None:
                msg = "kaboom"
                raise RuntimeError(msg)

        engine = _make_engine(rules=(_ExplodingRule(),))  # type: ignore[arg-type]
        ctx = _make_context()

        verdict = engine.evaluate(ctx)

        assert verdict.verdict == SecurityVerdictType.DENY
        assert verdict.risk_level == ApprovalRiskLevel.CRITICAL
        assert "fail-closed" in verdict.reason

    def test_exception_does_not_stop_subsequent_rules(self) -> None:
        """A failing rule produces DENY, short-circuiting remaining rules."""

        class _ExplodingRule:
            @property
            def name(self) -> str:
                return "exploding"

            def evaluate(
                self,
                context: SecurityContext,
            ) -> SecurityVerdict | None:
                msg = "kaboom"
                raise RuntimeError(msg)

        second_rule = _StubRule("second", verdict=None)
        engine = _make_engine(
            rules=(_ExplodingRule(), second_rule),  # type: ignore[arg-type]
        )
        ctx = _make_context()

        verdict = engine.evaluate(ctx)

        # The failing rule returns DENY which short-circuits.
        assert verdict.verdict == SecurityVerdictType.DENY
        assert not second_rule.called


@pytest.mark.unit
class TestRuleEngineSoftAllow:
    """Soft-allow from policy_validator does not short-circuit."""

    def test_soft_allow_continues_to_next_rule(self) -> None:
        from synthorg.security.rules.policy_validator import (
            _RULE_NAME as POLICY_VALIDATOR_NAME,
        )

        allow_verdict = SecurityVerdict(
            verdict=SecurityVerdictType.ALLOW,
            reason="Auto-approved by policy",
            risk_level=ApprovalRiskLevel.LOW,
            evaluated_at=datetime.now(UTC),
            evaluation_duration_ms=0.0,
        )
        soft_rule = _StubRule(POLICY_VALIDATOR_NAME, verdict=allow_verdict)
        deny_rule = _StubRule("deny-rule", verdict=_make_deny_verdict("blocked"))
        engine = _make_engine(rules=(soft_rule, deny_rule))
        ctx = _make_context()

        verdict = engine.evaluate(ctx)

        # Soft-allow should NOT prevent DENY from subsequent rule.
        assert verdict.verdict == SecurityVerdictType.DENY
        assert soft_rule.called
        assert deny_rule.called

    def test_soft_allow_used_when_no_deny(self) -> None:
        from synthorg.security.rules.policy_validator import (
            _RULE_NAME as POLICY_VALIDATOR_NAME,
        )

        allow_verdict = SecurityVerdict(
            verdict=SecurityVerdictType.ALLOW,
            reason="Auto-approved by policy",
            risk_level=ApprovalRiskLevel.LOW,
            evaluated_at=datetime.now(UTC),
            evaluation_duration_ms=0.0,
        )
        soft_rule = _StubRule(POLICY_VALIDATOR_NAME, verdict=allow_verdict)
        pass_rule = _StubRule("pass", verdict=None)
        engine = _make_engine(rules=(soft_rule, pass_rule))
        ctx = _make_context()

        verdict = engine.evaluate(ctx)

        assert verdict.verdict == SecurityVerdictType.ALLOW
        assert "Auto-approved by policy" in verdict.reason
        assert soft_rule.called
        assert pass_rule.called
