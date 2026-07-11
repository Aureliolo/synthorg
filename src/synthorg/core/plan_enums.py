# module-kind: code
"""Plan lifecycle enumerations."""

from enum import StrEnum


class PlanStatus(StrEnum):
    """Lifecycle status of a decomposed plan through CEO review.

    A plan is DRAFT while it is being shaped, PENDING_REVIEW once it is parked
    for the operator's decision, and terminal once decided. SUPERSEDED marks a
    plan replaced by a revised version (a re-plan after "request changes"), so
    the history of what was proposed is preserved rather than overwritten.
    """

    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
