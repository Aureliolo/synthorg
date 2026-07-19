"""Tests for the output-style hardening: fail-closed fallback, finding cap, and
model invariants.

The em-dash is built at runtime (``chr(0x2014)``) so no literal U+2014 lands in
committed test source.
"""

import pytest
from pydantic import ValidationError as PydanticValidationError

from synthorg.engine.output_style.evaluator import MAX_FINDINGS, OutputPolicyEvaluator
from synthorg.engine.output_style.exemptions import OutputContext
from synthorg.engine.output_style.models import (
    EnforcementMode,
    ExemptionScopeKind,
    OutputChannel,
    OutputPolicyFinding,
    OutputPolicyVerdict,
    OutputStyleConfig,
    OutputStyleRule,
    RulePack,
    RuleSeverity,
    RuleType,
    SanctionedExemption,
    SegmentKind,
)
from synthorg.engine.output_style.pack_loader import minimal_failclosed_pack
from synthorg.engine.output_style.rewriter import RewriteOp
from synthorg.engine.output_style.segmenter import Segment
from synthorg.engine.output_style.service import OutputStylePolicyService

_EM_DASH = chr(0x2014)


def _finding(*, exempted: bool, reason: str | None) -> OutputPolicyFinding:
    return OutputPolicyFinding(
        rule_id="r",
        rule_type=RuleType.LITERAL_BAN,
        severity=RuleSeverity.WARNING,
        mode=EnforcementMode.REJECT_REWORK,
        message="no em-dash",
        match_text="x",
        segment_kind=SegmentKind.PROSE,
        exempted=exempted,
        exemption_reason=reason,
    )


class TestFailClosedPack:
    @pytest.mark.unit
    def test_failclosed_pack_blocks_emdash(self) -> None:
        service = OutputStylePolicyService(
            pack=minimal_failclosed_pack(), config=OutputStyleConfig()
        )
        verdict = service.evaluate(
            f"shipping {_EM_DASH} done", OutputContext(channel=OutputChannel.MESSAGE)
        )
        assert verdict.blocked is True

    @pytest.mark.unit
    def test_failclosed_pack_allows_clean(self) -> None:
        service = OutputStylePolicyService(
            pack=minimal_failclosed_pack(), config=OutputStyleConfig()
        )
        verdict = service.evaluate(
            "shipping: done", OutputContext(channel=OutputChannel.MESSAGE)
        )
        assert verdict.clean is True


class TestFindingCap:
    @pytest.mark.unit
    def test_max_findings_bounds_the_list(self) -> None:
        rule = OutputStyleRule(
            id="emdash",
            type=RuleType.LITERAL_BAN,
            patterns=(_EM_DASH,),
            message="no em-dash",
        )
        evaluator = OutputPolicyEvaluator(rules=(rule,))
        text = (_EM_DASH + "x") * (MAX_FINDINGS + 50)
        verdict = evaluator.evaluate(text, OutputContext(channel=OutputChannel.MESSAGE))
        assert len(verdict.findings) == MAX_FINDINGS


class TestModelInvariants:
    @pytest.mark.unit
    def test_exempted_finding_requires_reason(self) -> None:
        with pytest.raises(PydanticValidationError):
            _finding(exempted=True, reason=None)

    @pytest.mark.unit
    def test_non_exempt_finding_rejects_reason(self) -> None:
        with pytest.raises(PydanticValidationError):
            _finding(exempted=False, reason="why")

    @pytest.mark.unit
    def test_segment_rejects_length_mismatch(self) -> None:
        with pytest.raises(PydanticValidationError):
            Segment(text="abc", kind=SegmentKind.PROSE, start=0, end=2)

    @pytest.mark.unit
    def test_rewrite_op_rejects_inverted_span(self) -> None:
        with pytest.raises(PydanticValidationError):
            RewriteOp(start=5, end=2, replacement="x")

    @pytest.mark.unit
    def test_pack_rejects_exemption_for_unknown_rule(self) -> None:
        with pytest.raises(PydanticValidationError):
            RulePack(
                name="p",
                version="1",
                rules=(
                    OutputStyleRule(
                        id="known",
                        type=RuleType.LITERAL_BAN,
                        patterns=("x",),
                        message="m",
                    ),
                ),
                exemptions=(
                    SanctionedExemption(
                        rule_id="does_not_exist",
                        scope_kind=ExemptionScopeKind.PATH,
                        match="src/**",
                        reason="typo",
                    ),
                ),
            )

    @pytest.mark.unit
    def test_summary_is_derived_from_findings(self) -> None:
        verdict = OutputPolicyVerdict(
            channel=OutputChannel.MESSAGE,
            findings=(_finding(exempted=False, reason=None),),
        )
        assert "no em-dash" in verdict.summary
        assert verdict.blocked is True
