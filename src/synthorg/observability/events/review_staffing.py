"""Review-staffing reconciler events.

A task parked because nobody held a gate's role waits on staffing, not on a
human decision, so something has to keep asking. These name what each pass
released, what it left parked and why, and what it did about the gap.
"""

from typing import Final

REVIEW_STAFFING_SWEEP_STARTED: Final[str] = "review_staffing.sweep.started"
REVIEW_STAFFING_SWEEP_COMPLETE: Final[str] = "review_staffing.sweep.complete"
REVIEW_STAFFING_SCHEDULER_STARTED: Final[str] = "review_staffing.scheduler.started"
REVIEW_STAFFING_SCHEDULER_STOPPED: Final[str] = "review_staffing.scheduler.stopped"
REVIEW_STAFFING_SCHEDULER_FAILED: Final[str] = "review_staffing.scheduler.failed"
REVIEW_STAFFING_SWEEP_PAUSED: Final[str] = "review_staffing.sweep.paused"
REVIEW_STAFFING_TASK_RELEASED: Final[str] = "review_staffing.task.released"
REVIEW_STAFFING_TASK_RELEASE_FAILED: Final[str] = "review_staffing.task.release_failed"
REVIEW_STAFFING_TASK_STILL_PARKED: Final[str] = "review_staffing.task.still_parked"
REVIEW_STAFFING_REJUDGED: Final[str] = "review_staffing.task.rejudged"
REVIEW_STAFFING_REJUDGE_FAILED: Final[str] = "review_staffing.task.rejudge_failed"
REVIEW_STAFFING_REJUDGE_SENT_BACK: Final[str] = "review_staffing.task.rejudge_sent_back"
REVIEW_STAFFING_ROLE_SWEEP_FAILED: Final[str] = "review_staffing.role.sweep_failed"
REVIEW_STAFFING_UNROUTABLE_ROLELESS: Final[str] = "review_staffing.unroutable.roleless"
REVIEW_STAFFING_ROLE_UNSTAFFED: Final[str] = "review_staffing.role.unstaffed"
REVIEW_STAFFING_HIRE_REQUESTED: Final[str] = "review_staffing.hire.requested"
REVIEW_STAFFING_HIRE_ALREADY_OPEN: Final[str] = "review_staffing.hire.already_open"
REVIEW_STAFFING_HIRE_REQUEST_FAILED: Final[str] = "review_staffing.hire.request_failed"
REVIEW_STAFFING_HIRE_COMPLETED: Final[str] = "review_staffing.hire.completed"
REVIEW_STAFFING_HIRE_COMPLETION_FAILED: Final[str] = (
    "review_staffing.hire.completion_failed"
)
REVIEW_STAFFING_NOTICE_FAILED: Final[str] = "review_staffing.notice.failed"
REVIEW_STAFFING_PROJECT_READ_FAILED: Final[str] = "review_staffing.project.read_failed"
