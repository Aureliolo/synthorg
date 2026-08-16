# module-kind: declarative
"""What makes an approval a review of a task's finished work.

Here rather than in the engine that mints them, for the same reason the
question types are: several layers need to recognise one and none owns the
others. The engine creates them, the review gate decides them, and the
approvals controller has to tell them apart from every other approval that
happens to name a task.

That last one is the whole point. A parked question and a plan approval both
carry the objective task's id, because that is what they are ABOUT, so
``task_id is not None`` does not mean "this is a review of finished work". The
completion gate's self-review preflight ran on that reading and warned about
tasks that were not reaching review at all; worse, it would have refused an
operator's answer to a question with "self-review is not permitted" had the
objective task ever carried the answering actor as its assignee.
"""

from typing import Final

#: A task finished and its work is awaiting a verdict.
REVIEW_COMPLETION_ACTION_TYPE: Final[str] = "review:task_completion"
#: A task failed and the failure is awaiting acknowledgement or a retry.
REVIEW_FAILED_ACTION_TYPE: Final[str] = "review:task_failed"
#: Every action type the completion review gate decides.
REVIEW_ACTION_TYPES: Final[tuple[str, ...]] = (
    REVIEW_COMPLETION_ACTION_TYPE,
    REVIEW_FAILED_ACTION_TYPE,
)


def is_task_review(action_type: str) -> bool:
    """Return whether *action_type* asks for a verdict on a task's own work.

    Returns:
        ``True`` when the approval is the completion review gate's, so the
        gate's rules (no self-review, the IN_REVIEW transition) apply to it.
    """
    return action_type in REVIEW_ACTION_TYPES


__all__ = [
    "REVIEW_ACTION_TYPES",
    "REVIEW_COMPLETION_ACTION_TYPE",
    "REVIEW_FAILED_ACTION_TYPE",
    "is_task_review",
]
