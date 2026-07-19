"""Unit tests for output-style policy models and validators."""

import pytest
from pydantic import ValidationError

from synthorg.engine.output_style.models import (
    EnforcementMode,
    HouseStyleDirective,
    OutputChannel,
    OutputPolicyFinding,
    OutputPolicyVerdict,
    OutputStyleRule,
    RulePack,
    RuleSeverity,
    RuleType,
    ScopeKind,
    SegmentKind,
)


def _rule(**overrides: object) -> OutputStyleRule:
    """Build a rule with sensible defaults, overridable per test."""
    fields: dict[str, object] = {
        "id": "r1",
        "type": RuleType.LITERAL_BAN,
        "patterns": ("bad",),
        "message": "no bad",
    }
    fields.update(overrides)
    return OutputStyleRule(**fields)  # type: ignore[arg-type]


class TestOutputStyleRule:
    @pytest.mark.unit
    def test_defaults_are_reject_and_scan_code(self) -> None:
        rule = _rule()
        assert rule.mode is EnforcementMode.REJECT_REWORK
        assert rule.scan_code is True
        assert rule.severity is RuleSeverity.WARNING

    @pytest.mark.unit
    def test_empty_patterns_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _rule(patterns=())

    @pytest.mark.unit
    def test_auto_rewrite_requires_rewrite_value(self) -> None:
        with pytest.raises(ValidationError):
            _rule(mode=EnforcementMode.AUTO_REWRITE, rewrite=None)

    @pytest.mark.unit
    def test_auto_rewrite_with_rewrite_ok(self) -> None:
        rule = _rule(mode=EnforcementMode.AUTO_REWRITE, rewrite=", ")
        assert rule.rewrite == ", "

    @pytest.mark.unit
    def test_frozen(self) -> None:
        rule = _rule()
        with pytest.raises(ValidationError):
            rule.id = "other"  # type: ignore[misc]


class TestHouseStyleDirective:
    @pytest.mark.unit
    def test_org_wide_default(self) -> None:
        directive = HouseStyleDirective(id="d1", text="be concise")
        assert directive.scope_kind is ScopeKind.ALL
        assert directive.scope == "all"

    @pytest.mark.unit
    def test_role_scope_ok(self) -> None:
        directive = HouseStyleDirective(
            id="d1", text="formal", scope="Legal", scope_kind=ScopeKind.ROLE
        )
        assert directive.scope == "Legal"

    @pytest.mark.unit
    def test_all_kind_with_non_all_scope_rejected(self) -> None:
        with pytest.raises(ValidationError):
            HouseStyleDirective(id="d1", text="x", scope="eng")

    @pytest.mark.unit
    def test_role_kind_with_all_scope_rejected(self) -> None:
        with pytest.raises(ValidationError):
            HouseStyleDirective(
                id="d1", text="x", scope="all", scope_kind=ScopeKind.ROLE
            )


class TestRulePack:
    @pytest.mark.unit
    def test_duplicate_rule_ids_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RulePack(
                name="p",
                version="1.0.0",
                rules=(_rule(id="dup"), _rule(id="dup")),
            )

    @pytest.mark.unit
    def test_duplicate_directive_ids_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RulePack(
                name="p",
                version="1.0.0",
                house_style=(
                    HouseStyleDirective(id="dup", text="a"),
                    HouseStyleDirective(id="dup", text="b"),
                ),
            )


class TestVerdict:
    @pytest.mark.unit
    def test_reject_finding_blocks(self) -> None:
        finding = OutputPolicyFinding(
            rule_id="r1",
            rule_type=RuleType.LITERAL_BAN,
            severity=RuleSeverity.CRITICAL,
            mode=EnforcementMode.REJECT_REWORK,
            message="no",
            match_text="x",
            segment_kind=SegmentKind.PROSE,
        )
        assert finding.blocks is True
        verdict = OutputPolicyVerdict(
            channel=OutputChannel.DELIVERABLE, findings=(finding,)
        )
        assert verdict.blocked is True
        assert verdict.clean is False

    @pytest.mark.unit
    def test_shadow_finding_never_blocks(self) -> None:
        finding = OutputPolicyFinding(
            rule_id="r1",
            rule_type=RuleType.REGEX_BAN,
            severity=RuleSeverity.INFO,
            mode=EnforcementMode.SHADOW,
            message="soft",
            match_text="x",
            segment_kind=SegmentKind.PROSE,
        )
        assert finding.blocks is False
        verdict = OutputPolicyVerdict(
            channel=OutputChannel.MESSAGE, findings=(finding,)
        )
        assert verdict.blocked is False

    @pytest.mark.unit
    def test_exempt_finding_never_blocks(self) -> None:
        finding = OutputPolicyFinding(
            rule_id="r1",
            rule_type=RuleType.LITERAL_BAN,
            severity=RuleSeverity.CRITICAL,
            mode=EnforcementMode.REJECT_REWORK,
            message="no",
            match_text="x",
            segment_kind=SegmentKind.CODE,
            exempted=True,
            exemption_reason="building a filter",
        )
        assert finding.blocks is False

    @pytest.mark.unit
    def test_clean_verdict(self) -> None:
        verdict = OutputPolicyVerdict(channel=OutputChannel.DELIVERABLE)
        assert verdict.clean is True
        assert verdict.blocked is False
