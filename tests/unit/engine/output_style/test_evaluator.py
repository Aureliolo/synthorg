"""Unit tests for the deterministic output-policy evaluator."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from synthorg.engine.output_style.evaluator import OutputPolicyEvaluator
from synthorg.engine.output_style.exemptions import OutputContext
from synthorg.engine.output_style.models import (
    EnforcementMode,
    ExemptionScopeKind,
    OutputChannel,
    OutputPolicyFinding,
    OutputStyleRule,
    RuleType,
    SanctionedExemption,
    SegmentKind,
)

#: Built at runtime so no literal U+2014 lands in committed test source.
_EM_DASH = chr(0x2014)

#: Read off the model so the bound cannot be asserted against a stale copy.
_CONTEXT_FIELD_MAX = OutputPolicyFinding.model_fields["context"].metadata[0].max_length


def _emdash_rule(
    mode: EnforcementMode = EnforcementMode.REJECT_REWORK,
) -> OutputStyleRule:
    return OutputStyleRule(
        id="emdash_literal",
        type=RuleType.LITERAL_BAN,
        patterns=(_EM_DASH,),
        message="Em-dash banned",
        mode=mode,
        rewrite=", " if mode is EnforcementMode.AUTO_REWRITE else None,
        scan_code=True,
        case_insensitive=False,
    )


def _long_match_rule() -> OutputStyleRule:
    """A rule whose pattern can match an unbounded span, as a regex may."""
    return OutputStyleRule(
        id="long_span",
        type=RuleType.REGEX_BAN,
        patterns=(r"BEGIN.*END",),
        message="Long span banned",
        mode=EnforcementMode.REJECT_REWORK,
        scan_code=True,
        case_insensitive=False,
    )


class TestQuotedContextStaysWithinItsField:
    """The window is built around the match, so the match must be bounded.

    ``match_text`` is truncated before it is stored; the context window was
    not, and it is built from the raw span plus a radius either side. A rule
    matching a long unbroken run therefore produced a string longer than
    ``OutputPolicyFinding.context`` accepts, and evaluation raised instead
    of returning the verdict it was asked for, which fails the output
    boundary open on an operator-authored rule pack.
    """

    @pytest.mark.unit
    def test_a_long_match_still_returns_a_verdict(self) -> None:
        ev = OutputPolicyEvaluator(rules=(_long_match_rule(),))
        text = "BEGIN " + ("filler " * 200) + "END"

        verdict = ev.evaluate(text, OutputContext(channel=OutputChannel.DELIVERABLE))

        assert verdict.blocked is True
        assert verdict.findings
        assert verdict.findings[0].context

    @pytest.mark.unit
    def test_the_window_never_outgrows_the_field(self) -> None:
        ev = OutputPolicyEvaluator(rules=(_long_match_rule(),))
        text = "BEGIN " + ("x" * 4000) + " END"

        verdict = ev.evaluate(text, OutputContext(channel=OutputChannel.DELIVERABLE))

        for finding in verdict.findings:
            assert len(finding.context) <= _CONTEXT_FIELD_MAX


class TestHardBan:
    @pytest.mark.unit
    def test_emdash_in_prose_blocks(self) -> None:
        ev = OutputPolicyEvaluator(rules=(_emdash_rule(),))
        text = f"The parser {_EM_DASH} rewritten {_EM_DASH} now works."
        verdict = ev.evaluate(text, OutputContext(channel=OutputChannel.DELIVERABLE))
        assert verdict.blocked is True
        assert len([f for f in verdict.findings if f.blocks]) == 2
        assert verdict.summary

    @pytest.mark.unit
    def test_the_summary_quotes_what_has_to_change(self) -> None:
        """A rejection the author cannot act on is a delayed failure.

        The rework loop hands this summary back with "address that
        specifically", so naming only the rule sends the author hunting for
        a character in a whole deliverable. A live run spent three rework
        rounds and half a million tokens never finding four of them, and the
        task failed with its peer review already approved.
        """
        ev = OutputPolicyEvaluator(rules=(_emdash_rule(),))
        text = f"The board renders {_EM_DASH} eventually {_EM_DASH} at 60 fps."
        verdict = ev.evaluate(text, OutputContext(channel=OutputChannel.DELIVERABLE))

        assert "renders" in verdict.summary
        assert "eventually" in verdict.summary

    @pytest.mark.unit
    def test_clean_prose_passes(self) -> None:
        ev = OutputPolicyEvaluator(rules=(_emdash_rule(),))
        verdict = ev.evaluate(
            "A clean sentence: nothing to see here.",
            OutputContext(channel=OutputChannel.DELIVERABLE),
        )
        assert verdict.clean is True
        assert verdict.blocked is False

    @pytest.mark.unit
    def test_emdash_in_commit_message_blocks(self) -> None:
        ev = OutputPolicyEvaluator(rules=(_emdash_rule(),))
        verdict = ev.evaluate(
            f"fix: tidy the parser {_EM_DASH} again",
            OutputContext(channel=OutputChannel.COMMIT_MESSAGE),
        )
        assert verdict.blocked is True

    @pytest.mark.unit
    def test_codepoint_reference_is_not_a_violation(self) -> None:
        # A textual reference to the codepoint is ASCII, not the character.
        ev = OutputPolicyEvaluator(rules=(_emdash_rule(),))
        verdict = ev.evaluate(
            "strip the U+2014 escape from output",
            OutputContext(channel=OutputChannel.DELIVERABLE),
        )
        assert verdict.clean is True


class TestModes:
    @pytest.mark.unit
    def test_shadow_never_blocks(self) -> None:
        ev = OutputPolicyEvaluator(rules=(_emdash_rule(mode=EnforcementMode.SHADOW),))
        verdict = ev.evaluate(
            f"prose {_EM_DASH} here",
            OutputContext(channel=OutputChannel.DELIVERABLE),
        )
        assert verdict.blocked is False
        assert len(verdict.findings) == 1
        assert verdict.findings[0].mode is EnforcementMode.SHADOW

    @pytest.mark.unit
    def test_global_shadow_mode_forces_shadow(self) -> None:
        ev = OutputPolicyEvaluator(rules=(_emdash_rule(),), shadow_mode=True)
        verdict = ev.evaluate(
            f"prose {_EM_DASH} here",
            OutputContext(channel=OutputChannel.DELIVERABLE),
        )
        assert verdict.blocked is False
        assert verdict.findings[0].mode is EnforcementMode.SHADOW

    @pytest.mark.unit
    def test_auto_rewrite_fixes_prose(self) -> None:
        ev = OutputPolicyEvaluator(
            rules=(_emdash_rule(mode=EnforcementMode.AUTO_REWRITE),)
        )
        text = f"The parser {_EM_DASH} now works."
        verdict = ev.evaluate(text, OutputContext(channel=OutputChannel.DELIVERABLE))
        assert verdict.blocked is False
        assert verdict.rewritten_text == "The parser ,  now works."
        assert _EM_DASH not in (verdict.rewritten_text or "")

    @pytest.mark.unit
    def test_auto_rewrite_in_code_downgrades_to_reject(self) -> None:
        ev = OutputPolicyEvaluator(
            rules=(_emdash_rule(mode=EnforcementMode.AUTO_REWRITE),)
        )
        verdict = ev.evaluate(
            f'label = "{_EM_DASH}"',
            OutputContext(channel=OutputChannel.CODE_FILE),
        )
        assert verdict.blocked is True
        assert verdict.rewritten_text is None
        assert verdict.findings[0].mode is EnforcementMode.REJECT_REWORK

    @pytest.mark.unit
    def test_auto_rewrite_inside_fenced_code_downgrades(self) -> None:
        ev = OutputPolicyEvaluator(
            rules=(_emdash_rule(mode=EnforcementMode.AUTO_REWRITE),)
        )
        text = f"see below\n```\nx = '{_EM_DASH}'\n```\n"
        verdict = ev.evaluate(text, OutputContext(channel=OutputChannel.DELIVERABLE))
        assert verdict.blocked is True
        assert verdict.findings[0].segment_kind is SegmentKind.CODE


class TestScanCode:
    @pytest.mark.unit
    def test_prose_only_rule_skips_code_segment(self) -> None:
        rule = OutputStyleRule(
            id="delve",
            type=RuleType.REGEX_BAN,
            patterns=("(?<![a-z])delve(?![a-z])",),
            message="no delve",
            mode=EnforcementMode.SHADOW,
            scan_code=False,
        )
        ev = OutputPolicyEvaluator(rules=(rule,))
        verdict = ev.evaluate(
            "call `delve` here",
            OutputContext(channel=OutputChannel.DELIVERABLE),
        )
        assert verdict.clean is True


class TestExemptions:
    @pytest.mark.unit
    def test_sanctioned_scope_exempts(self) -> None:
        exemption = SanctionedExemption(
            rule_id="emdash_literal",
            scope_kind=ExemptionScopeKind.PATH,
            match="src/textfilter/**",
            reason="filter product",
        )
        ev = OutputPolicyEvaluator(rules=(_emdash_rule(),), exemptions=(exemption,))
        verdict = ev.evaluate(
            f'BANNED = "{_EM_DASH}"',
            OutputContext(
                channel=OutputChannel.CODE_FILE,
                file_path="src/textfilter/strip.py",
            ),
        )
        assert verdict.blocked is False
        assert verdict.findings[0].exempted is True
        assert verdict.findings[0].exemption_reason == "filter product"

    @pytest.mark.unit
    def test_outside_scope_not_exempt(self) -> None:
        exemption = SanctionedExemption(
            rule_id="emdash_literal",
            scope_kind=ExemptionScopeKind.PATH,
            match="src/textfilter/**",
            reason="filter product",
        )
        ev = OutputPolicyEvaluator(rules=(_emdash_rule(),), exemptions=(exemption,))
        verdict = ev.evaluate(
            f'x = "{_EM_DASH}"',
            OutputContext(
                channel=OutputChannel.CODE_FILE,
                file_path="src/other/mod.py",
            ),
        )
        assert verdict.blocked is True


class TestCompilation:
    @pytest.mark.unit
    def test_invalid_regex_rejected_at_rule_construction(self) -> None:
        # An invalid regex fails when the rule is built (where the pack loads),
        # not later at first evaluation, so a bad pack fails loudly at its source.
        with pytest.raises(PydanticValidationError):
            OutputStyleRule(
                id="bad",
                type=RuleType.REGEX_BAN,
                patterns=("(unclosed",),
                message="bad regex",
            )

    @pytest.mark.unit
    def test_catastrophic_regex_rejected_at_rule_construction(self) -> None:
        with pytest.raises(PydanticValidationError):
            OutputStyleRule(
                id="redos",
                type=RuleType.REGEX_BAN,
                patterns=("(a+)+b",),
                message="catastrophic",
            )
