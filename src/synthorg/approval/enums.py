"""Approval workflow enumerations."""

from enum import StrEnum


class ApprovalStatus(StrEnum):
    """Status of a human approval item."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalRiskLevel(StrEnum):
    """Risk level assigned to an approval item."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalSource(StrEnum):
    """Origin of an approval item, fixed at creation.

    Routing of a decided approval (mid-execution resume vs. review
    gate) keys off this persisted discriminator rather than a live
    parked-context probe, keeping the flow deterministic.

    Attributes:
        PARKED_CONTEXT: Backs a parked agent execution context (SecOps
            escalation or ``request_human_approval``); resumes the run.
        REVIEW_GATE: Any other approval (autonomy, hiring, promotion,
            scaling, ...); drives the review-gate transition. Default.
        CONVERSATIONAL_INTAKE: A work item proposed via the
            conversational interface; approval rebuilds the ``WorkItem``
            and runs it through the pipeline, rejection declines it.
        CONVERSATIONAL_INVITE: An agent's request to add another agent
            to a group conversation; approval adds the participant +
            hands over the transcript, rejection leaves membership.
    """

    PARKED_CONTEXT = "parked_context"
    REVIEW_GATE = "review_gate"
    CONVERSATIONAL_INTAKE = "conversational_intake"
    CONVERSATIONAL_INVITE = "conversational_invite"
