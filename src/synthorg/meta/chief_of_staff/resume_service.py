# module-kind: service
"""Repo facade for the conversational approval-resume flows.

The approvals controller resolves a decided conversational approval
(a steering directive or an agent invite) through the resume flows in
``api/controllers/_conversational_resume.py``. This thin service is the
single seam those flows route every invite / participant repo call
through, so the controller layer never touches a repository protocol.

The service is deliberately *ungated*: it wraps only the persistence
repositories (never the toggle-gated Chief-of-Staff feature services),
so a decided conversational approval still resolves after its feature is
switched off. It is wired once at startup alongside the repositories it
wraps (``_wire_conversational_repositories_and_reconcile``) and is
absent only when persistence cannot back the repositories at all -- in
which case the controller's 503 path fires, matching the prior
per-repo ``is None`` guards.

It lives in the ``meta`` package (not ``api/services/``) so the early
``api_core_state`` import chain never pulls the ``communication`` enum
modules these protocols depend on, which would otherwise trip the
cold-import cycle gate.
"""

from collections.abc import Mapping, Sequence
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.enums import (
    ConversationInviteStatus,
    ConversationParticipantStatus,
    ParticipantAdmission,
)
from synthorg.meta.chief_of_staff.group_models import (
    ConversationInvite,
    ConversationParticipant,
)
from synthorg.meta.chief_of_staff.models import (
    Conversation,
    ConversationTurn,
)
from synthorg.observability import get_logger
from synthorg.observability.events.chief_of_staff import (
    COS_RESUME_INVITE_TRANSITION,
    COS_RESUME_PARTICIPANT_ADMITTED,
)
from synthorg.persistence.conversation_invite_protocol import (
    ConversationInviteFilterSpec,
    ConversationInviteRepository,
)
from synthorg.persistence.conversation_participant_protocol import (
    ConversationParticipantFilterSpec,
    ConversationParticipantRepository,
)
from synthorg.persistence.conversation_protocol import (
    ConversationRepository,
    ConversationTurnFilterSpec,
    ConversationTurnRepository,
)

logger = get_logger(__name__)

#: The turn that opened a conversation. Every intake path appends the human's
#: own message first, at ``len(prior_turns)``, so a conversation's first row is
#: both position zero and the operator's own words.
_OPENING_SEQUENCE: Final[int] = 0


class ConversationalResumeService:
    """Ungated repo facade for the conversational approval-resume flows."""

    __slots__ = (
        "_conversation_repo",
        "_invite_repo",
        "_participant_repo",
        "_turn_repo",
    )

    def __init__(
        self,
        *,
        invite_repo: ConversationInviteRepository,
        participant_repo: ConversationParticipantRepository,
        conversation_repo: ConversationRepository,
        turn_repo: ConversationTurnRepository,
    ) -> None:
        self._invite_repo = invite_repo
        self._participant_repo = participant_repo
        self._conversation_repo = conversation_repo
        self._turn_repo = turn_repo

    async def owner_conversations(
        self,
        *,
        created_by: NotBlankStr,
        limit: int,
        offset: int,
    ) -> tuple[Conversation, ...]:
        """List one owner's conversations, newest-first, for the drawer.

        Returns:
            The owner's conversation headers within the page window.
        """
        return await self._conversation_repo.list_items(
            created_by=created_by,
            limit=limit,
            offset=offset,
        )

    async def get_conversation(
        self,
        conversation_id: NotBlankStr,
    ) -> Conversation | None:
        """Fetch one conversation header (owner check is the caller's).

        Returns:
            The conversation, or ``None`` when no such row exists.
        """
        return await self._conversation_repo.get(conversation_id)

    async def conversation_turns(
        self,
        *,
        conversation_id: NotBlankStr,
        limit: int,
        offset: int,
    ) -> tuple[ConversationTurn, ...]:
        """Page one conversation's turns for the resume drawer.

        Returns:
            The turns within the page window.
        """
        return await self._turn_repo.query(
            ConversationTurnFilterSpec(conversation_id=conversation_id),
            limit=limit,
            offset=offset,
        )

    async def opening_turns(
        self,
        conversations: Sequence[Conversation],
        *,
        created_by: NotBlankStr,
    ) -> Mapping[str, ConversationTurn]:
        """Fetch the turn that opened each of *conversations*.

        One query for the whole page. The drawer names every row from its own
        opening sentence, so a per-row read would put the page's cost on how
        many conversations the operator has had.

        Scoped here rather than trusted from the caller. The turn carries what
        a person typed and the header is the only row that says whose it is,
        so a method taking bare ids would answer any id it was handed and the
        next caller that assembles them from anywhere but its own owner-scoped
        page is one line from reading somebody else's words. Taking the
        headers keeps the check where the answer is.

        Sequence ``0`` is the operator's own words: every intake path appends
        the human turn before anything else, at ``len(prior_turns)``, which is
        zero for a conversation that is being opened.

        Args:
            conversations: The headers the page is about.
            created_by: The owner the page was read for. A header belonging to
                anybody else contributes nothing.

        Returns:
            The opening turn per conversation id. A conversation whose opening
            turn a retention purge removed is simply absent, which the caller
            reads as "nothing names this one".
        """
        owned = tuple(
            NotBlankStr(str(c.id)) for c in conversations if c.created_by == created_by
        )
        if not owned:
            return {}
        turns = await self._turn_repo.query(
            ConversationTurnFilterSpec(
                conversation_ids=owned,
                sequence=_OPENING_SEQUENCE,
            ),
            limit=len(owned),
        )
        return {turn.conversation_id: turn for turn in turns}

    async def invites_for_approval(
        self,
        approval_id: str,
    ) -> tuple[ConversationInvite, ...]:
        """Return the invite rows backing a decided consent approval.

        Returns:
            The matching invites (empty when none back the approval).
        """
        return await self._invite_repo.query(
            ConversationInviteFilterSpec(approval_id=approval_id),
        )

    async def transition_invite(
        self,
        invite_id: str,
        *,
        from_status: ConversationInviteStatus,
        to_status: ConversationInviteStatus,
    ) -> bool:
        """Atomically CAS an invite between two statuses.

        Returns:
            ``True`` when this caller won the transition, ``False`` when
            a concurrent writer had already moved the invite.
        """
        won = await self._invite_repo.transition_if(
            invite_id,
            from_status,
            to_status,
        )
        logger.info(
            COS_RESUME_INVITE_TRANSITION,
            invite_id=invite_id,
            from_status=from_status.value,
            to_status=to_status.value,
            won=won,
        )
        return won

    async def active_participants(
        self,
        conversation_id: str,
    ) -> tuple[ConversationParticipant, ...]:
        """Return the active roster of a group conversation.

        Returns:
            The active participant rows for the conversation.
        """
        return await self._participant_repo.query(
            ConversationParticipantFilterSpec(
                conversation_id=conversation_id,
                status=ConversationParticipantStatus.ACTIVE,
            ),
        )

    async def add_participant(self, participant: ConversationParticipant) -> None:
        """Insert an active roster row (idempotent at the repo layer)."""
        await self._participant_repo.save(participant)

    async def admit_participant_within_cap(
        self,
        participant: ConversationParticipant,
        *,
        cap: int,
    ) -> ParticipantAdmission:
        """Atomically admit *participant* iff the roster is under *cap*.

        Routes through the repository's transactional admit so the
        already-member check, the active-count read, and the insert are a
        single atomic unit -- two concurrent consents cannot both pass the
        cap and push the roster to ``cap + 1``.

        Returns:
            The admission outcome (admitted / already-active / cap-reached).
        """
        admission = await self._participant_repo.admit_active_within_cap(
            participant, cap=cap
        )
        logger.info(
            COS_RESUME_PARTICIPANT_ADMITTED,
            conversation_id=participant.conversation_id,
            participant_id=participant.agent_id,
            cap=cap,
            outcome=admission.value,
        )
        return admission
