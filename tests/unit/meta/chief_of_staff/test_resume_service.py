"""Unit tests for :class:`ConversationalResumeService`.

The service is a thin, deliberately-ungated facade the approvals
resume flows route every proposal / invite / participant repository
call through. These tests pin that each method delegates to the right
repository with the right filter / transition arguments, so the
controller layer never has to touch a repository protocol directly.
"""

import pytest

from synthorg.communication.conversation.enums import ConversationalProposalStatus
from synthorg.meta.chief_of_staff.enums import (
    ConversationInviteStatus,
    ConversationParticipantStatus,
)
from synthorg.meta.chief_of_staff.group_models import ConversationParticipant
from synthorg.meta.chief_of_staff.resume_service import ConversationalResumeService
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
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _service(
    *,
    proposal_repo: ConversationalProposalRepository | None = None,
    invite_repo: ConversationInviteRepository | None = None,
    participant_repo: ConversationParticipantRepository | None = None,
) -> ConversationalResumeService:
    """Build the service, defaulting any unsupplied repo to a typed mock."""
    return ConversationalResumeService(
        proposal_repo=proposal_repo or mock_of[ConversationalProposalRepository](),
        invite_repo=invite_repo or mock_of[ConversationInviteRepository](),
        participant_repo=participant_repo
        or mock_of[ConversationParticipantRepository](),
    )


async def test_proposals_for_approval_filters_by_approval_id() -> None:
    repo = mock_of[ConversationalProposalRepository]()
    repo.query.return_value = ()
    await _service(proposal_repo=repo).proposals_for_approval("appr-1")
    repo.query.assert_awaited_once_with(
        ConversationalProposalFilterSpec(approval_id="appr-1"),
    )


async def test_transition_proposal_delegates_cas() -> None:
    repo = mock_of[ConversationalProposalRepository]()
    repo.transition_if.return_value = True
    won = await _service(proposal_repo=repo).transition_proposal(
        "prop-1",
        from_status=ConversationalProposalStatus.PENDING,
        to_status=ConversationalProposalStatus.EXECUTED,
    )
    assert won is True
    repo.transition_if.assert_awaited_once_with(
        "prop-1",
        ConversationalProposalStatus.PENDING,
        ConversationalProposalStatus.EXECUTED,
    )


async def test_invites_for_approval_filters_by_approval_id() -> None:
    repo = mock_of[ConversationInviteRepository]()
    repo.query.return_value = ()
    await _service(invite_repo=repo).invites_for_approval("appr-9")
    repo.query.assert_awaited_once_with(
        ConversationInviteFilterSpec(approval_id="appr-9"),
    )


async def test_transition_invite_delegates_cas() -> None:
    repo = mock_of[ConversationInviteRepository]()
    repo.transition_if.return_value = False
    won = await _service(invite_repo=repo).transition_invite(
        "inv-1",
        from_status=ConversationInviteStatus.PENDING,
        to_status=ConversationInviteStatus.ACCEPTED,
    )
    assert won is False
    repo.transition_if.assert_awaited_once_with(
        "inv-1",
        ConversationInviteStatus.PENDING,
        ConversationInviteStatus.ACCEPTED,
    )


async def test_active_participants_filters_active_status() -> None:
    repo = mock_of[ConversationParticipantRepository]()
    repo.query.return_value = ()
    await _service(participant_repo=repo).active_participants("conv-1")
    repo.query.assert_awaited_once_with(
        ConversationParticipantFilterSpec(
            conversation_id="conv-1",
            status=ConversationParticipantStatus.ACTIVE,
        ),
    )


async def test_add_participant_saves_row() -> None:
    repo = mock_of[ConversationParticipantRepository]()
    participant = mock_of[ConversationParticipant]()
    await _service(participant_repo=repo).add_participant(participant)
    repo.save.assert_awaited_once_with(participant)
