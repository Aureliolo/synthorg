# module-kind: service
"""Boundary enforcement for the output-style policy.

The single helper every agent-output boundary calls before the output escapes
(a message publish, a commit, a PR body, a completed deliverable). It reads the
ambient service, evaluates the output, audits the verdict, and either raises
``OutputPolicyViolationError`` (PreToolUse-style instant rejection the producing
agent sees and reworks against) or returns the possibly auto-rewritten text.

A non-raising variant (:func:`evaluate_output_policy`) is used by the completion
backstop gate, which maps the verdict onto the review chain's reroute idiom
rather than raising.
"""

from synthorg.engine.output_style.errors import OutputPolicyViolationError
from synthorg.engine.output_style.exemptions import OutputContext
from synthorg.engine.output_style.models import EnforcementMode, OutputPolicyVerdict
from synthorg.engine.output_style.service import current_output_policy_service
from synthorg.observability import get_logger
from synthorg.observability.events.output_style import (
    OUTPUT_STYLE_EXEMPTION_GRANTED,
    OUTPUT_STYLE_GATE_REJECTED,
    OUTPUT_STYLE_VIOLATION_REWRITTEN,
    OUTPUT_STYLE_VIOLATION_SHADOWED,
)

logger = get_logger(__name__)

_DEFAULT_MESSAGE = "Output violates a hard output-style rule"


def _audit(verdict: OutputPolicyVerdict, ctx: OutputContext) -> None:
    """Emit structured audit events for a verdict's non-clean findings."""
    for finding in verdict.findings:
        if finding.exempted:
            logger.info(
                OUTPUT_STYLE_EXEMPTION_GRANTED,
                channel=ctx.channel.value,
                rule_id=finding.rule_id,
                reason=finding.exemption_reason,
            )
        elif finding.mode is EnforcementMode.SHADOW:
            logger.info(
                OUTPUT_STYLE_VIOLATION_SHADOWED,
                channel=ctx.channel.value,
                rule_id=finding.rule_id,
                severity=finding.severity.value,
            )
    if verdict.blocked:
        logger.warning(
            OUTPUT_STYLE_GATE_REJECTED,
            channel=ctx.channel.value,
            rule_ids=[f.rule_id for f in verdict.findings if f.blocks],
        )
    elif verdict.rewritten_text is not None:
        logger.info(OUTPUT_STYLE_VIOLATION_REWRITTEN, channel=ctx.channel.value)


def evaluate_output_policy(text: str, ctx: OutputContext) -> OutputPolicyVerdict | None:
    """Evaluate output at a boundary without raising.

    Args:
        text: The agent-produced output.
        ctx: The output context.

    Returns:
        The verdict, or ``None`` when the policy is unwired or disabled (the
        completion backstop treats ``None`` as a clean pass-through).
    """
    service = current_output_policy_service()
    if service is None or not service.enabled:
        return None
    verdict = service.evaluate(text, ctx)
    _audit(verdict, ctx)
    return verdict


def enforce_output_policy(text: str, ctx: OutputContext) -> str:
    """Enforce the policy at an output boundary, raising on a hard violation.

    Args:
        text: The agent-produced output about to escape the boundary.
        ctx: The output context.

    Returns:
        The original text, or the auto-rewritten text when a rule resolved a
        prose violation. Unchanged when the policy is unwired or disabled.

    Raises:
        OutputPolicyViolationError: When a non-exempt hard rule blocks; the
            message carries the specific rule reasons for the agent to rework.
    """
    verdict = evaluate_output_policy(text, ctx)
    if verdict is None:
        return text
    if verdict.blocked:
        raise OutputPolicyViolationError(verdict.summary or _DEFAULT_MESSAGE)
    if verdict.rewritten_text is not None:
        return verdict.rewritten_text
    return text


__all__ = ["enforce_output_policy", "evaluate_output_policy"]
