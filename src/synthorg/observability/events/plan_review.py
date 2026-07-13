"""Plan-review panel event constants.

The stakeholder-review phase: a bounded panel of leads reviews a built plan
before it reaches the human approver.
"""

from typing import Final

PLAN_REVIEW_PANEL_SELECTED: Final[str] = "plan_review.panel.selected"
PLAN_REVIEW_PANEL_EMPTY: Final[str] = "plan_review.panel.empty"
PLAN_REVIEW_PANEL_STARTED: Final[str] = "plan_review.panel.started"
PLAN_REVIEW_PANEL_COMPLETED: Final[str] = "plan_review.panel.completed"
PLAN_REVIEW_REVIEWER_STARTED: Final[str] = "plan_review.reviewer.started"
PLAN_REVIEW_REVIEWER_COMPLETED: Final[str] = "plan_review.reviewer.completed"
PLAN_REVIEW_REVIEWER_NO_VERDICT: Final[str] = "plan_review.reviewer.no_verdict"
PLAN_REVIEW_REVIEWER_DUPLICATE_SUBMIT: Final[str] = (
    "plan_review.reviewer.duplicate_submit"
)
PLAN_REVIEW_VALIDATION_ERROR: Final[str] = "plan_review.validation.error"
