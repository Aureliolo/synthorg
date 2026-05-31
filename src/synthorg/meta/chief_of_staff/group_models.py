# module-kind: declarative
"""Domain + boundary models for the multi-agent group chat (#1970).

Kept separate from ``models.py`` (the v1 clarify-and-propose models) so
the group-chat / invite surfaces can grow their schema without pushing
the proposer models past their size tier. ``ConversationParticipant`` is
the durable roster entity (persisted by the participant repositories);
the ``GroupConverse*`` pair is the ``GroupChatService.converse``
boundary; ``AttributedContribution`` is one agent's attributed turn
within a single round.
"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.enums import (
    ConversationParticipantStatus,
    GroupChatTruncationReason,
)


class ConversationParticipant(BaseModel):
    """An agent enrolled in a group conversation.

    The roster row mutated by the group-chat round loop (initial
    enrolment) and the agent-invite consent flow (#1971). ``status``
    flips ``active`` <-> ``removed`` via the repository compare-and-set
    so membership changes are atomic.

    Attributes:
        id: Unique participant-row identifier.
        conversation_id: Owning group conversation id.
        agent_id: Enrolled agent's identity id.
        agent_name: Human-readable display name of the agent.
        participant_role: The agent's role label (e.g. ``CFO``), used
            for attribution in the shared transcript.
        status: Membership state (active or removed).
        added_by: User or agent id that added this participant.
        added_at: When the participant was enrolled.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    conversation_id: NotBlankStr
    agent_id: NotBlankStr
    agent_name: NotBlankStr
    participant_role: NotBlankStr
    status: ConversationParticipantStatus = ConversationParticipantStatus.ACTIVE
    added_by: NotBlankStr
    added_at: AwareDatetime


class AttributedContribution(BaseModel):
    """One agent's attributed contribution within a single round.

    Mirrors the ``AGENT`` ``ConversationTurn`` that is persisted for the
    contribution, surfaced to the caller so the API/UI can render the
    attribution without re-querying the turn store.

    Attributes:
        agent_id: The contributing agent's identity id.
        agent_name: Human-readable name of the contributing agent.
        participant_role: The agent's role label.
        content: The contribution text.
        sequence: Zero-based turn index within the conversation.
        input_tokens: Prompt tokens this contribution consumed.
        output_tokens: Response tokens this contribution generated.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr
    agent_name: NotBlankStr
    participant_role: NotBlankStr
    content: NotBlankStr
    sequence: int = Field(ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class GroupConverseArgs(BaseModel):
    """Args model for one ``GroupChatService.converse`` round.

    Attributes:
        message: The human's natural-language message this round.
        created_by: User id that owns the conversation.
        conversation_id: Existing group conversation to continue, or
            ``None`` to open a new one.
        participants: Initial agent ids to enrol; required (non-empty)
            when opening a new conversation, ignored when continuing
            (the stored roster is authoritative).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    message: NotBlankStr
    created_by: NotBlankStr
    conversation_id: NotBlankStr | None = None
    participants: tuple[NotBlankStr, ...] = ()


class GroupConverseResult(BaseModel):
    """Outcome of one group-chat round.

    Attributes:
        conversation_id: The group conversation this round belongs to.
        contributions: Attributed contributions in turn order (the
            agents that actually spoke this round).
        participants: The active roster after this round, for the UI.
        participants_skipped: Agent ids that did NOT contribute because a
            round bound tripped mid-round; empty when the round ran in
            full. Surfaced so a truncated round never reads as complete.
        truncated_reason: Why the round stopped early, or ``None`` when
            every active participant contributed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    conversation_id: NotBlankStr
    contributions: tuple[AttributedContribution, ...] = ()
    participants: tuple[ConversationParticipant, ...] = ()
    participants_skipped: tuple[NotBlankStr, ...] = ()
    truncated_reason: GroupChatTruncationReason | None = None


__all__ = [
    "AttributedContribution",
    "ConversationParticipant",
    "GroupConverseArgs",
    "GroupConverseResult",
]
