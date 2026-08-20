# module-kind: code
"""Deterministic output-policy evaluator (no LLM in this path).

Compiles a pack's hard rules once, matches them per prose / code segment,
resolves sanctioned exemptions, and returns an :class:`OutputPolicyVerdict`.
Enforcement modes:

* ``REJECT_REWORK`` (default) blocks a non-exempt match.
* ``SHADOW`` records the finding but never blocks (fuzzy heuristics).
* ``AUTO_REWRITE`` deterministically fixes a prose match; the same rule in a
  code segment downgrades to ``REJECT_REWORK`` so code is never rewritten.

A global ``shadow_mode`` forces every rule to ``SHADOW`` for an observation
period. Banned literals are matched via escaped regex so offsets stay aligned
to the original text for reporting and rewriting.
"""

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from synthorg.engine.output_style._regex_safety import compile_checked
from synthorg.engine.output_style.errors import OutputStylePackValidationError
from synthorg.engine.output_style.exemptions import ExemptionResolver, OutputContext
from synthorg.engine.output_style.models import (
    EnforcementMode,
    OutputPolicyFinding,
    OutputPolicyVerdict,
    OutputStyleRule,
    RuleType,
    SanctionedExemption,
    SegmentKind,
)
from synthorg.engine.output_style.rewriter import RewriteOp, apply_rewrites
from synthorg.engine.output_style.segmenter import Segment, segment
from synthorg.observability import get_logger
from synthorg.observability.events.output_style import OUTPUT_STYLE_FINDINGS_TRUNCATED

logger = get_logger(__name__)

#: Upper bound on findings retained per evaluation (defends against a
#: pathological input producing an unbounded finding list). Scanning stops once
#: the cap is reached, so the cap bounds compute cost, not just list size.
MAX_FINDINGS: Final[int] = 100

#: Bound on the offending snippet recorded on each finding.
_MATCH_SNIPPET_LIMIT: Final[int] = 200

#: Characters kept either side of a match so the author can find the place.
#: A clause's worth: enough to be unambiguous in a long document, short
#: enough that a handful of them stay a reason rather than a transcript.
_CONTEXT_RADIUS: Final[int] = 60

#: Longest match the window quotes whole, before its middle is elided. The
#: radius either side plus this has to stay inside the bound on
#: ``OutputPolicyFinding.context``, or building the finding raises and takes
#: the whole verdict with it.
_MAX_QUOTED_MATCH: Final[int] = 200


def _context_window(text: str, start: int, end: int) -> str:
    """Return the match with the words around it, on one line.

    Whitespace is collapsed because the window is quoted back inside a
    single-line rework reason, and a match on a line boundary would
    otherwise break the reason across the paragraph it came from.

    Args:
        text: The segment the match was found in.
        start: Inclusive start offset of the match.
        end: Exclusive end offset of the match.

    Returns:
        The surrounding text, elided at either end that was cut.
    """
    left = max(0, start - _CONTEXT_RADIUS)
    right = min(len(text), end + _CONTEXT_RADIUS)
    window = " ".join(_bounded_span(text, start, end, left, right).split())
    if not window:
        return ""
    opening = "..." if left > 0 else ""
    closing = "..." if right < len(text) else ""
    return f"{opening}{window}{closing}"


def _bounded_span(text: str, start: int, end: int, left: int, right: int) -> str:
    """Return the window's raw text with an over-long match elided.

    A regex rule may match an arbitrarily long run, and the window is built
    AROUND the match, so an unbounded one renders a string longer than
    ``OutputPolicyFinding.context`` accepts and evaluation raises rather
    than returning a verdict. Both ends of the match are kept, because what
    locates it in a deliverable is where it starts and where it stops.

    Args:
        text: The segment the match was found in.
        start: Inclusive start offset of the match.
        end: Exclusive end offset of the match.
        left: Inclusive start offset of the window.
        right: Exclusive end offset of the window.

    Returns:
        The window text, with the middle of a long match replaced.
    """
    if end - start <= _MAX_QUOTED_MATCH:
        return text[left:right]
    half = _MAX_QUOTED_MATCH // 2
    return (
        f"{text[left:start]}{text[start : start + half]}"
        f" ... {text[end - half : end]}{text[end:right]}"
    )


class OutputPolicyEvaluator:
    """Evaluates agent output against a compiled set of hard rules."""

    def __init__(
        self,
        *,
        rules: tuple[OutputStyleRule, ...],
        exemptions: tuple[SanctionedExemption, ...] = (),
        shadow_mode: bool = False,
    ) -> None:
        """Compile the rule patterns and prepare the exemption resolver.

        Args:
            rules: The active hard rules.
            exemptions: Merged (pack + operator) sanctioned exemptions.
            shadow_mode: When true, every rule is forced to SHADOW.

        Raises:
            OutputStylePackValidationError: If a regex rule fails to compile or
                is rejected as unsafe.
        """
        self._rules = rules
        self._shadow_mode = shadow_mode
        self._resolver = ExemptionResolver(exemptions)
        compiled_map: dict[str, tuple[re.Pattern[str], ...]] = {}
        for rule in rules:
            compiled: list[re.Pattern[str]] = []
            for pattern in rule.patterns:
                try:
                    if rule.type is RuleType.LITERAL_BAN:
                        flags = re.IGNORECASE if rule.case_insensitive else 0
                        compiled.append(re.compile(re.escape(pattern), flags))
                    else:
                        compiled.append(
                            compile_checked(
                                pattern, case_insensitive=rule.case_insensitive
                            )
                        )
                except (re.error, ValueError) as exc:
                    msg = f"Rule {rule.id!r} has an invalid regex pattern {pattern!r}"
                    raise OutputStylePackValidationError(msg) from exc
            compiled_map[rule.id] = tuple(compiled)
        self._compiled: Mapping[str, tuple[re.Pattern[str], ...]] = MappingProxyType(
            compiled_map
        )

    def evaluate(self, text: str, ctx: OutputContext) -> OutputPolicyVerdict:
        """Evaluate one piece of agent output.

        Args:
            text: The agent-produced output.
            ctx: The output context (channel + exemption-scope fields).

        Returns:
            The verdict: findings and an optional rewritten text.
        """
        segments = segment(text, ctx.channel)
        findings: list[OutputPolicyFinding] = []
        rewrite_ops: list[RewriteOp] = []

        for rule in self._rules:
            budget = MAX_FINDINGS - len(findings)
            if budget <= 0:
                break
            exemption = self._resolver.resolve(rule.id, ctx)
            rule_findings, rule_ops = self._scan_rule(
                rule, exemption, segments, budget, text
            )
            findings.extend(rule_findings)
            rewrite_ops.extend(rule_ops)

        if len(findings) >= MAX_FINDINGS:
            logger.warning(
                OUTPUT_STYLE_FINDINGS_TRUNCATED,
                channel=ctx.channel.value,
                cap=MAX_FINDINGS,
            )

        rewritten = apply_rewrites(text, rewrite_ops) if rewrite_ops else None
        return OutputPolicyVerdict(
            channel=ctx.channel,
            findings=tuple(findings),
            rewritten_text=rewritten,
        )

    def _scan_rule(
        self,
        rule: OutputStyleRule,
        exemption: SanctionedExemption | None,
        segments: tuple[Segment, ...],
        budget: int,
        text: str,
    ) -> tuple[list[OutputPolicyFinding], list[RewriteOp]]:
        """Scan one rule over the segments, up to ``budget`` findings.

        Args:
            rule: The rule to scan.
            exemption: The sanctioned exemption covering it, when one does.
            segments: The prose/code spans of *text*.
            budget: How many more findings may be produced.
            text: The whole evaluated output, for the line each match sits on.

        Returns:
            The findings and any auto-rewrite ops produced by this rule;
            scanning stops as soon as ``budget`` findings accumulate so a
            pathological input is bounded in compute, not just list size.
        """
        findings: list[OutputPolicyFinding] = []
        ops: list[RewriteOp] = []
        for seg in segments:
            if seg.kind is SegmentKind.CODE and not rule.scan_code:
                continue
            for compiled in self._compiled[rule.id]:
                for match in compiled.finditer(seg.text):
                    if match.start() == match.end():
                        continue
                    if len(findings) >= budget:
                        return findings, ops
                    mode = self._effective_mode(rule.mode, seg.kind)
                    findings.append(
                        OutputPolicyFinding(
                            rule_id=rule.id,
                            rule_type=rule.type,
                            severity=rule.severity,
                            mode=mode,
                            message=rule.message,
                            match_text=match.group(0)[:_MATCH_SNIPPET_LIMIT],
                            context=_context_window(
                                seg.text, match.start(), match.end()
                            ),
                            segment_kind=seg.kind,
                            line=text.count("\n", 0, seg.start + match.start()) + 1,
                            exempted=exemption is not None,
                            exemption_reason=(
                                exemption.reason if exemption is not None else None
                            ),
                        )
                    )
                    if (
                        mode is EnforcementMode.AUTO_REWRITE
                        and exemption is None
                        and seg.kind is SegmentKind.PROSE
                        and rule.rewrite is not None
                    ):
                        ops.append(
                            RewriteOp(
                                start=seg.start + match.start(),
                                end=seg.start + match.end(),
                                replacement=rule.rewrite,
                            )
                        )
        return findings, ops

    def _effective_mode(
        self, declared: EnforcementMode, seg_kind: SegmentKind
    ) -> EnforcementMode:
        """Resolve a rule's mode after shadow override and code downgrade.

        Returns:
            ``SHADOW`` under global shadow mode; otherwise the declared mode,
            except an ``AUTO_REWRITE`` in a code segment downgrades to
            ``REJECT_REWORK`` so code is never silently rewritten.
        """
        if self._shadow_mode:
            return EnforcementMode.SHADOW
        if declared is EnforcementMode.AUTO_REWRITE and seg_kind is SegmentKind.CODE:
            return EnforcementMode.REJECT_REWORK
        return declared


__all__ = ["MAX_FINDINGS", "OutputPolicyEvaluator"]
