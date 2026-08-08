# module-kind: code
"""The plan-level context every task under a plan inherits.

``plan.assumptions`` and ``plan.open_questions`` were written by the planner,
persisted, and rendered in the dashboard, and no agent ever saw either. That
made the escalation surface half a mechanism: an operator answering a parked
question had the answer written onto ``plan.assumptions``, and the agents
doing the work read their own item description and nothing else, so the
answer reached the plan and the plan delivered it nowhere.

The context is appended to each derived task's description rather than
carried in a field of its own. The description is the one thing every prompt
path already renders and already fences (``format_task_instruction`` wraps it
in ``<task-data>``), so a human's words cross the LLM boundary as data on
exactly the same terms as the task text they arrive with, with no second
fencing site to keep in step.
"""

from collections.abc import Sequence
from typing import Final

_SETTLED_HEADING: Final[str] = "## Settled before this work was dispatched"
_SETTLED_PREAMBLE: Final[str] = (
    "These hold for this plan. Some were the planner's assumptions and some "
    "are answers a human gave to questions the planner could not resolve; "
    "either way they are decided. Build on them rather than re-deciding them."
)

_OPEN_HEADING: Final[str] = "## Not decided yet"
_OPEN_PREAMBLE: Final[str] = (
    "Nobody has answered these. Do not invent an answer and proceed as though "
    "it were settled: if one blocks your task, say so in your result rather "
    "than guessing."
)


def plan_context_block(
    *,
    assumptions: Sequence[str],
    open_questions: Sequence[str],
) -> str:
    """Render a plan's settled and unsettled context, or nothing.

    Args:
        assumptions: What the plan rests on, including answers written back
            from settled questions.
        open_questions: What is still unanswered at dispatch.

    Returns:
        A markdown block, or the empty string when the plan has neither.
    """
    sections: list[str] = []
    if assumptions:
        sections.append(_SETTLED_HEADING)
        sections.append(_SETTLED_PREAMBLE)
        sections.extend(f"- {assumption}" for assumption in assumptions)
    if open_questions:
        if sections:
            sections.append("")
        sections.append(_OPEN_HEADING)
        sections.append(_OPEN_PREAMBLE)
        sections.extend(f"- {question}" for question in open_questions)
    return "\n".join(sections)


def with_plan_context(
    description: str,
    *,
    assumptions: Sequence[str],
    open_questions: Sequence[str],
) -> str:
    """Append a plan's context to one task description.

    Args:
        description: The item's own description.
        assumptions: The plan's settled context.
        open_questions: The plan's unsettled context.

    Returns:
        The description, followed by the context block when there is one.
    """
    block = plan_context_block(assumptions=assumptions, open_questions=open_questions)
    if not block:
        return description
    return f"{description}\n\n{block}"


__all__ = ["plan_context_block", "with_plan_context"]
