"""Events for assembling the deliverable a reviewer judges.

The deliverable reader and the review-input builder feed every review gate,
including the completion oracle's peer reviewer, which is on by default. Their
events belong to that shared path rather than to any one gate: logging a
workspace read failure under a gate's own namespace would file it against a
gate that may not even be configured.
"""

from typing import Final

# The project workspace could not be read, so the declared deliverables were
# not verified. ERROR, not WARNING: the review still happens, and what it is
# missing needs to be alertable.
DELIVERABLE_READ_FAILED: Final[str] = "deliverable.read_failed"

# No reviewable deliverable could be assembled for a task (no assignee, no
# acceptance criteria, or no recorded run). Routine, hence INFO.
DELIVERABLE_NOT_REVIEWABLE: Final[str] = "deliverable.not_reviewable"

__all__ = [
    "DELIVERABLE_NOT_REVIEWABLE",
    "DELIVERABLE_READ_FAILED",
]
