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
from typing import Final

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
from synthorg.engine.output_style.segmenter import segment

#: Upper bound on findings retained per evaluation (defends against a
#: pathological input producing an unbounded finding list).
MAX_FINDINGS: Final[int] = 100

#: Bound on the offending snippet recorded on each finding.
_MATCH_SNIPPET_LIMIT: Final[int] = 200


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
            OutputStylePackValidationError: If a regex rule fails to compile.
        """
        self._rules = rules
        self._shadow_mode = shadow_mode
        self._resolver = ExemptionResolver(exemptions)
        self._compiled: dict[str, tuple[re.Pattern[str], ...]] = {}
        for rule in rules:
            flags = re.IGNORECASE if rule.case_insensitive else 0
            compiled: list[re.Pattern[str]] = []
            for pattern in rule.patterns:
                source = (
                    re.escape(pattern) if rule.type is RuleType.LITERAL_BAN else pattern
                )
                try:
                    compiled.append(re.compile(source, flags))
                except re.error as exc:
                    msg = f"Rule {rule.id!r} has an invalid regex pattern {pattern!r}"
                    raise OutputStylePackValidationError(msg) from exc
            self._compiled[rule.id] = tuple(compiled)

    def evaluate(self, text: str, ctx: OutputContext) -> OutputPolicyVerdict:
        """Evaluate one piece of agent output.

        Args:
            text: The agent-produced output.
            ctx: The output context (channel + exemption-scope fields).

        Returns:
            The verdict: findings, an optional rewritten text, and a summary.
        """
        segments = segment(text, ctx.channel)
        findings: list[OutputPolicyFinding] = []
        rewrite_ops: list[RewriteOp] = []

        for rule in self._rules:
            exemption = self._resolver.resolve(rule.id, ctx)
            for seg in segments:
                if seg.kind is SegmentKind.CODE and not rule.scan_code:
                    continue
                for compiled in self._compiled[rule.id]:
                    for match in compiled.finditer(seg.text):
                        if match.start() == match.end():
                            continue
                        if len(findings) >= MAX_FINDINGS:
                            break
                        mode = self._effective_mode(rule.mode, seg.kind)
                        findings.append(
                            OutputPolicyFinding(
                                rule_id=rule.id,
                                rule_type=rule.type,
                                severity=rule.severity,
                                mode=mode,
                                message=rule.message,
                                match_text=match.group(0)[:_MATCH_SNIPPET_LIMIT],
                                segment_kind=seg.kind,
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
                            rewrite_ops.append(
                                RewriteOp(
                                    start=seg.start + match.start(),
                                    end=seg.start + match.end(),
                                    replacement=rule.rewrite,
                                )
                            )

        rewritten = apply_rewrites(text, rewrite_ops) if rewrite_ops else None
        return OutputPolicyVerdict(
            channel=ctx.channel,
            findings=tuple(findings),
            rewritten_text=rewritten,
            summary=self._summary(findings),
        )

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

    @staticmethod
    def _summary(findings: list[OutputPolicyFinding]) -> str:
        """Build the agent-facing reason for a blocked verdict.

        Returns:
            A one-line summary of the distinct blocking rule messages, or an
            empty string when nothing blocks.
        """
        messages: list[str] = []
        for finding in findings:
            if finding.blocks and finding.message not in messages:
                messages.append(finding.message)
        if not messages:
            return ""
        return "Output-style policy rejected this output: " + "; ".join(messages)


__all__ = ["MAX_FINDINGS", "OutputPolicyEvaluator"]
