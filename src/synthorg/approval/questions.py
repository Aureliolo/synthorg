# module-kind: declarative
"""What makes an approval a parked agent question.

The action types live here rather than in either tool because four layers need
them and none of them owns the others: the two tools that create a question,
the chat surface that lists and answers one, and the resume router that has to
tell a declined question apart from a rejected action.
"""

from typing import Final

#: An agent stopped to ask a human a free-text question.
CLARIFY_ACTION_TYPE: Final[str] = "clarify:question"
#: An agent stopped to ask a human to pick between project directions.
DECISION_ACTION_TYPE: Final[str] = "decision:project"
#: Every action type that means "an agent is waiting on a human answer".
QUESTION_ACTION_TYPES: Final[tuple[str, ...]] = (
    CLARIFY_ACTION_TYPE,
    DECISION_ACTION_TYPE,
)

#: Server-owned guidance for a question the operator declined to answer.
#:
#: Carried on the resume's TRUSTED channel, never inside the untrusted fence.
#: A declined question resumes with a REJECTED verdict, which on its own reads
#: as "do not proceed"; the agent is in fact meant to proceed on its own
#: judgement. Putting that instruction inside the fence would hand the model a
#: directive under a banner telling it to disregard directives, and would train
#: it that fenced content is sometimes meant to be obeyed, which is exactly
#: what makes a genuinely hostile reason more likely to be followed.
DECLINED_QUESTION_NOTE: Final[str] = (
    "No answer is coming for this question. Proceed on your own best "
    "judgement, and state the assumption you made in your next output."
)


def is_question(action_type: str) -> bool:
    """Return whether *action_type* marks an approval as a parked question.

    Returns:
        ``True`` when an agent is waiting on a human answer.
    """
    return action_type in QUESTION_ACTION_TYPES


__all__ = [
    "CLARIFY_ACTION_TYPE",
    "DECISION_ACTION_TYPE",
    "DECLINED_QUESTION_NOTE",
    "QUESTION_ACTION_TYPES",
    "is_question",
]
