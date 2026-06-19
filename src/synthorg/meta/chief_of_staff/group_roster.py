# module-kind: code
"""Roster + transcript helpers for the multi-agent group chat.

Free functions kept separate from :class:`GroupChatService` so each
concern stays within its module-size tier. These cover the membership side of a
group conversation -- resolving agent ids to identities, enrolling the
initial roster in stable order, reading the active roster, and reading
the ordered transcript -- plus the participant de-duplication used when
a conversation is opened. The service owns the round loop and the
conversation-lifecycle decisions; these are the cohesive data-access
helpers it calls through.
"""

from datetime import datetime, timedelta

from synthorg.core.agent import AgentIdentity
from synthorg.core.collections import dedupe_preserving_order
from synthorg.core.types import NotBlankStr
from synthorg.hr.registry import AgentRegistryService
from synthorg.meta.chief_of_staff.enums import ConversationParticipantStatus
from synthorg.meta.chief_of_staff.group_models import ConversationParticipant
from synthorg.meta.chief_of_staff.models import ConversationTurn
from synthorg.meta.errors import GroupParticipantUnknownError
from synthorg.observability import get_logger
from synthorg.observability.events.chief_of_staff import COS_GROUP_PARTICIPANTS_ADDED
from synthorg.persistence.conversation_participant_protocol import (
    ConversationParticipantFilterSpec,
    ConversationParticipantRepository,
)
from synthorg.persistence.conversation_protocol import (
    ConversationTurnFilterSpec,
    ConversationTurnRepository,
)

logger = get_logger(__name__)

# Group conversations are short interactive sessions; 1000 turns is a
# generous ceiling that hands every round the full history without
# pagination (the repo's own _MAX_PAGE_LIMIT clamps anything larger).
MAX_TURNS_QUERY_LIMIT: int = 1000


def dedupe_participants(participants: tuple[NotBlankStr, ...]) -> list[NotBlankStr]:
    """Drop duplicate agent ids, preserving first-seen order.

    Returns:
        The de-duplicated agent ids.
    """
    return list(dedupe_preserving_order(participants))


async def resolve_identities(
    agent_registry: AgentRegistryService,
    agent_ids: list[NotBlankStr],
) -> list[AgentIdentity]:
    """Resolve every agent id to its identity, or fail fast.

    Returns:
        The resolved identities, in the order supplied.

    Raises:
        GroupParticipantUnknownError: A named agent is not registered.
    """
    identities: list[AgentIdentity] = []
    for agent_id in agent_ids:
        identity = await agent_registry.get(agent_id)
        if identity is None:
            raise GroupParticipantUnknownError(agent_id=agent_id)
        identities.append(identity)
    return identities


async def enrol_participants(
    participant_repo: ConversationParticipantRepository,
    *,
    conversation_id: NotBlankStr,
    identities: list[AgentIdentity],
    added_by: NotBlankStr,
    now: datetime,
) -> None:
    """Persist an active participant row for each resolved identity.

    A per-index microsecond offset on ``added_at`` records enrolment
    order, so the roster query (``added_at ASC, id ASC``) walks the
    round in the order the caller listed participants rather than by
    the random participant uuid -- a batch otherwise shares one
    ``now`` and the order would be arbitrary.
    """
    for index, identity in enumerate(identities):
        await participant_repo.save(
            ConversationParticipant(
                conversation_id=conversation_id,
                agent_id=NotBlankStr(str(identity.id)),
                agent_name=identity.name,
                participant_role=identity.role,
                status=ConversationParticipantStatus.ACTIVE,
                added_by=added_by,
                added_at=now + timedelta(microseconds=index),
            )
        )
    logger.info(
        COS_GROUP_PARTICIPANTS_ADDED,
        conversation_id=conversation_id,
        count=len(identities),
    )


async def active_participants(
    participant_repo: ConversationParticipantRepository,
    conversation_id: NotBlankStr,
) -> tuple[ConversationParticipant, ...]:
    """Return the active roster for a conversation, enrolment order.

    Returns:
        Tuple of active participants, oldest-enrolled first.
    """
    return await participant_repo.query(
        ConversationParticipantFilterSpec(
            conversation_id=conversation_id,
            status=ConversationParticipantStatus.ACTIVE,
        )
    )


async def ordered_turns(
    turn_repo: ConversationTurnRepository,
    conversation_id: NotBlankStr,
) -> tuple[ConversationTurn, ...]:
    """Return all turns for a conversation, oldest-first.

    Returns:
        Tuple of turns sorted by sequence ascending.
    """
    newest_first = await turn_repo.query(
        ConversationTurnFilterSpec(conversation_id=conversation_id),
        limit=MAX_TURNS_QUERY_LIMIT,
    )
    return tuple(sorted(newest_first, key=lambda turn: turn.sequence))


__all__ = [
    "MAX_TURNS_QUERY_LIMIT",
    "active_participants",
    "dedupe_participants",
    "enrol_participants",
    "ordered_turns",
    "resolve_identities",
]
