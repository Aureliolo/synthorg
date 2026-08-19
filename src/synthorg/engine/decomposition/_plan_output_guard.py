# module-kind: code
"""The output-style guard for the prose a plan carries.

A plan is agent output an operator reads and approves: every item title,
description and done-when criterion, and every plan-level assumption and open
question, is written by a model and rendered on the plan page. Guarding a
commit message and a PR body while leaving the artefact the operator actually
decides on unguarded is the gap this closes; the run that found it produced a
plan whose title and four item titles carried the one punctuation mark the
policy ships a hard rule against.

It sits on the submit path rather than on the mapping to the durable plan,
even though the mapping is where every strategy converges, because WHERE a
rejection lands decides what it costs. On the submit path a violation is a
correctable tool error the planning agent reworks against, and for the
tool-less fallback it is a retry with the reason attached: the same bargain
every other boundary strikes with its producer. Past that point the producer
is gone, and the only thing left to reject is a finished decomposition, which
would spend a twenty-minute plan on a punctuation mark.
"""

from synthorg.core.types import NotBlankStr
from synthorg.engine.output_style.errors import OutputPolicyViolationError
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
        OutputPolicyViolationError: When a non-exempt hard rule blocks.
    """
    return NotBlankStr(enforce_output_policy(text, _PLAN_CONTEXT))


def guard_plan_texts(texts: tuple[NotBlankStr, ...]) -> tuple[NotBlankStr, ...]:
    """Guard each of *texts*.

    Args:
        texts: A plan's criteria, assumptions or open questions.

    Returns:
        The guarded texts, in order.

    Raises:
        OutputPolicyViolationError: When a non-exempt hard rule blocks.
    """
    return tuple(guard_plan_text(text) for text in texts)


def plan_style_refusal(exc: OutputPolicyViolationError) -> str:
    """Phrase a style rejection for the producer that can still fix it.

    Args:
        exc: The refusal the guard raised.

    Returns:
        A sentence naming the rule and what to do, for the submit tool's
        error result or the single-shot strategy's retry.
    """
    return f"The plan's wording breaks a house style rule: {exc}"


__all__ = ["guard_plan_text", "guard_plan_texts", "plan_style_refusal"]
