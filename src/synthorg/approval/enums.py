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


class QuestionReversibility(StrEnum):
    """How reversible the choice behind an agent's question is.

    Declared by the agent on ``request_clarification`` /
    ``request_project_decision`` and recorded on the resulting
    ``ApprovalItem`` metadata, so the operator sees at a glance whether an
    answer is undoable and the chat surface can rank the questions that are.

    It deliberately does not feed ``ApprovalRiskLevel``, which drives autonomy
    routing and the approval-timeout policy: escalating a hard-to-reverse
    question there would silently re-route parks that exist today.

    Attributes:
        REVERSIBLE: Undoing the choice is a quick edit.
        HARD_TO_REVERSE: Undoing the choice costs real rework.
    """

    REVERSIBLE = "reversible"
    HARD_TO_REVERSE = "hard_to_reverse"


class ApprovalSource(StrEnum):
    """Origin of an approval item, fixed at creation.

    Routing of a decided approval (mid-execution resume vs. review
    gate) keys off this persisted discriminator rather than a live
    parked-context probe, keeping the flow deterministic.

    Attributes:
        PARKED_CONTEXT: Backs a parked agent execution context (SecOps
            escalation or ``request_human_approval``); resumes the run.
        REVIEW_GATE: Any other approval (autonomy, hiring, promotion,
            ...); drives the review-gate transition. Default.
        CONVERSATIONAL_INTAKE: A work item proposed via the
            conversational interface; approval rebuilds the ``WorkItem``
            and runs it through the pipeline, rejection declines it.
        CONVERSATIONAL_INVITE: An agent's request to add another agent
            to a group conversation; approval adds the participant +
            hands over the transcript, rejection leaves membership.
        PLAN_REVIEW: A decomposed plan awaiting human approval before a
            team builds; approval dispatches the exact approved plan
            (``coordinate(precomputed_plan=...)``), rejection cancels it.
    """

    PARKED_CONTEXT = "parked_context"
    REVIEW_GATE = "review_gate"
    CONVERSATIONAL_INTAKE = "conversational_intake"
    CONVERSATIONAL_INVITE = "conversational_invite"
    PLAN_REVIEW = "plan_review"
