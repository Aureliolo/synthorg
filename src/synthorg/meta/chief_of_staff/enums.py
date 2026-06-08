"""Conversational-interface enums.

Feature-local enums for the concern-routing, group-chat, and
agent-invite surfaces. The conversational turn / status enums live in
``synthorg.communication.conversation.enums``.
"""

from enum import StrEnum


class ConversationKind(StrEnum):
    """Shape of a conversation, fixed at creation.

    Discriminates the three conversational surfaces that share the
    ``conversations`` / ``conversation_turns`` tables.

    Attributes:
        DIRECT: The v1 1:1 clarify-and-propose thread with the generic
            Chief of Staff persona. Default.
        ROUTED: A 1:1 thread whose turns are routed to a role agent by
            concern (budget to CFO, strategy to CEO, ...). The
            responding agent is recorded per assistant turn.
        GROUP: A multi-agent group conversation: one human, several
            participant agents, attributed ``AGENT`` turns.
    """

    DIRECT = "direct"
    ROUTED = "routed"
    GROUP = "group"


class ConversationParticipantStatus(StrEnum):
    """Membership state of an agent in a group conversation.

    Attributes:
        ACTIVE: The agent participates in turn-taking and receives the
            shared transcript.
        REMOVED: The agent was removed from the conversation; retained
            for audit but excluded from future rounds.
    """

    ACTIVE = "active"
    REMOVED = "removed"


class ConversationInviteStatus(StrEnum):
    """Lifecycle state of an agent-initiated group-chat invite.

    Attributes:
        PENDING: Awaiting the human consent decision. Acquired via the
            canonical approval queue.
        ACCEPTED: The human consented; the invited agent was added to
            the participant set via a PENDING -> ACCEPTED CAS.
        DECLINED: The human declined; membership is left unchanged.
    """

    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"


class GroupChatTruncationReason(StrEnum):
    """Why a group-chat round stopped before every participant spoke.

    A round is bounded so a single human turn cannot drive unbounded
    cost. When a bound trips mid-round the remaining participants are
    skipped and the reason is surfaced on the result (never silently);
    ``None`` on the result means the round completed in full.

    Attributes:
        TOKEN_BUDGET_EXHAUSTED: The per-round token budget was consumed
            before the remaining participants could contribute.
        MAX_TOTAL_TURNS_REACHED: Appending a further contribution would
            exceed the conversation's total-turn cap.
    """

    TOKEN_BUDGET_EXHAUSTED = "token_budget_exhausted"  # noqa: S105 -- enum label, not a secret
    MAX_TOTAL_TURNS_REACHED = "max_total_turns_reached"


__all__ = [
    "ConversationInviteStatus",
    "ConversationKind",
    "ConversationParticipantStatus",
    "GroupChatTruncationReason",
]
