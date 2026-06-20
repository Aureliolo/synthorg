# module-kind: service
"""Repo facade for the conversational approval-resume flows.

The approvals controller resolves a decided conversational approval
(intake proposal or agent invite) through the resume flows in
``api/controllers/_conversational_resume.py``. This thin service is the
single seam those flows route every proposal / invite / participant repo
call through, so the controller layer never touches a repository
protocol.

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

from synthorg.communication.conversation.enums import ConversationalProposalStatus
from synthorg.meta.chief_of_staff.enums import (
    ConversationInviteStatus,
    ConversationParticipantStatus,
    ParticipantAdmission,
)
from synthorg.meta.chief_of_staff.group_models import (
    ConversationInvite,
    ConversationParticipant,
)
from synthorg.meta.chief_of_staff.models import ConversationalProposal
from synthorg.persistence.conversation_invite_protocol import (
    ConversationInviteFilterSpec,
    ConversationInviteRepository,
)
from synthorg.persistence.conversation_participant_protocol import (
    ConversationParticipantFilterSpec,
    ConversationParticipantRepository,
)
from synthorg.persistence.conversational_proposal_protocol import (
    ConversationalProposalFilterSpec,
    ConversationalProposalRepository,
)


class ConversationalResumeService:
    """Ungated repo facade for the conversational approval-resume flows."""

    __slots__ = ("_invite_repo", "_participant_repo", "_proposal_repo")

    def __init__(
        self,
        *,
        proposal_repo: ConversationalProposalRepository,
        invite_repo: ConversationInviteRepository,
        participant_repo: ConversationParticipantRepository,
    ) -> None:
        self._proposal_repo = proposal_repo
        self._invite_repo = invite_repo
        self._participant_repo = participant_repo

    async def proposals_for_approval(
        self,
        approval_id: str,
    ) -> tuple[ConversationalProposal, ...]:
        """Return the proposal rows backing a decided intake approval.

        Returns:
            The matching proposals (empty when none back the approval).
        """
        return await self._proposal_repo.query(
            ConversationalProposalFilterSpec(approval_id=approval_id),
        )

    async def transition_proposal(
        self,
        proposal_id: str,
        *,
        from_status: ConversationalProposalStatus,
        to_status: ConversationalProposalStatus,
    ) -> bool:
        """Atomically CAS a proposal between two statuses.

        Returns:
            ``True`` when this caller won the transition, ``False`` when
            a concurrent writer had already moved the proposal.
        """
        return await self._proposal_repo.transition_if(
            proposal_id,
            from_status,
            to_status,
        )

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
        return await self._invite_repo.transition_if(
            invite_id,
            from_status,
            to_status,
        )

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
        return await self._participant_repo.admit_active_within_cap(
            participant, cap=cap
        )
