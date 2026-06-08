"""Conversation domain enumerations."""

from enum import StrEnum


class ConversationRole(StrEnum):
    """Author of a single conversational turn.

    Attributes:
        USER: A human message into the conversation.
        ASSISTANT: A Chief of Staff reply (generic 1:1 + routed paths).
        AGENT: A named role agent's group-chat contribution; the author
            is recorded in the turn's ``author_agent_id`` / name fields.
    """

    USER = "user"
    ASSISTANT = "assistant"
    AGENT = "agent"


class ConversationStatus(StrEnum):
    """Lifecycle state of a Chief of Staff conversation.

    Attributes:
        ACTIVE: Open for further turns; the clarify loop may continue.
        PROPOSED: At least one work item proposed into the approval
            queue from this conversation.
        CLOSED: Terminal; no further turns are accepted.
    """

    ACTIVE = "active"
    PROPOSED = "proposed"
    CLOSED = "closed"


class ConversationalProposalStatus(StrEnum):
    """Lifecycle state of a conversational work proposal.

    Attributes:
        PENDING: Awaiting the human approval decision.
        EXECUTING: Approved, pipeline run in flight. Acquired via
            PENDING -> EXECUTING CAS so only one concurrent decision
            drives the pipeline; reverted to PENDING on failure
            (retryable) or advanced to EXECUTED on success.
        EXECUTED: Approved; the work item ran through the pipeline.
        REJECTED: Declined; the work item never reached the pipeline.
    """

    PENDING = "pending"
    EXECUTING = "executing"
    EXECUTED = "executed"
    REJECTED = "rejected"
