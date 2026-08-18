# module-kind: code
"""The output-style guard for the prose a plan carries.

A plan is agent output an operator reads and approves: every item title,
description and done-when criterion, and every plan-level assumption and open
question, is written by a model and rendered on the plan page. Guarding a
commit message and a PR body while leaving the artefact the operator actually
decides on unguarded is the gap this closes; the run that found it produced a
plan whose first assumption carried the one punctuation mark the policy ships
a hard rule against.

The guard sits on the mapping from a decomposition to the durable plan, which
is the one place every planning strategy converges, so an alternative
decomposer cannot arrive with prose nobody checked.
"""

from synthorg.core.types import NotBlankStr
from synthorg.engine.output_style.exemptions import OutputContext
from synthorg.engine.output_style.interceptor import enforce_output_policy
from synthorg.engine.output_style.models import OutputChannel

#: A plan is prose, not code: it is read, not compiled. The deliverable
#: channel is the one whose segmenter isolates fenced code inside prose,
#: which is what an item description quoting a symbol needs.
_PLAN_CONTEXT: OutputContext = OutputContext(channel=OutputChannel.DELIVERABLE)


def guard_plan_text(text: NotBlankStr) -> NotBlankStr:
    """Return *text* fit to render on the plan, or raise.

    Args:
        text: One piece of plan prose the operator will read.

    Returns:
        The text, or its auto-rewrite when a rule resolved the violation.

    Raises:
        OutputPolicyViolationError: When a non-exempt hard rule blocks. The
            caller is the decomposition path, whose failure leaves a FAILED
            plan carrying the reason, so a blocked plan is visible rather
            than quietly reworded.
    """
    return NotBlankStr(enforce_output_policy(text, _PLAN_CONTEXT))


def guard_plan_texts(texts: tuple[NotBlankStr, ...]) -> tuple[NotBlankStr, ...]:
    """Guard each of *texts*.

    Args:
        texts: A plan's criteria, assumptions or open questions.

    Returns:
        The guarded texts, in order.
    """
    return tuple(guard_plan_text(text) for text in texts)


__all__ = ["guard_plan_text", "guard_plan_texts"]
