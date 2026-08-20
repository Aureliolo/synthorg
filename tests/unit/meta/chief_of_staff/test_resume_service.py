"""Unit tests for :class:`ConversationalResumeService`.

The service is a thin, deliberately-ungated facade the approvals
resume flows route every invite / participant repository call through.
These tests pin that each method delegates to the right repository with
the right filter / transition arguments, so the controller layer never
has to touch a repository protocol directly.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.enums import (
    ConversationInviteStatus,
    ConversationParticipantStatus,
)
from synthorg.meta.chief_of_staff.group_models import ConversationParticipant
from synthorg.meta.chief_of_staff.models import Conversation
from synthorg.meta.chief_of_staff.resume_service import ConversationalResumeService
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
from tests._shared import as_uuid, mock_of, sid

pytestmark = pytest.mark.unit


def _service(
    *,
    invite_repo: ConversationInviteRepository | None = None,
    participant_repo: ConversationParticipantRepository | None = None,
    conversation_repo: ConversationRepository | None = None,
    turn_repo: ConversationTurnRepository | None = None,
) -> ConversationalResumeService:
    """Build the service, defaulting any unsupplied repo to a typed mock."""
    return ConversationalResumeService(
        invite_repo=invite_repo or mock_of[ConversationInviteRepository](),
        participant_repo=participant_repo
        or mock_of[ConversationParticipantRepository](),
        conversation_repo=conversation_repo or mock_of[ConversationRepository](),
        turn_repo=turn_repo or mock_of[ConversationTurnRepository](),
    )


async def test_invites_for_approval_filters_by_approval_id() -> None:
    repo = mock_of[ConversationInviteRepository]()
    repo.query.return_value = ()
    await _service(invite_repo=repo).invites_for_approval(sid("appr-9"))
    repo.query.assert_awaited_once_with(
        ConversationInviteFilterSpec(approval_id=sid("appr-9")),
    )


async def test_transition_invite_delegates_cas() -> None:
    repo = mock_of[ConversationInviteRepository]()
    repo.transition_if.return_value = False
    won = await _service(invite_repo=repo).transition_invite(
        sid("inv-1"),
        from_status=ConversationInviteStatus.PENDING,
        to_status=ConversationInviteStatus.ACCEPTED,
    )
    assert won is False
    repo.transition_if.assert_awaited_once_with(
        sid("inv-1"),
        ConversationInviteStatus.PENDING,
        ConversationInviteStatus.ACCEPTED,
    )


async def test_active_participants_filters_active_status() -> None:
    repo = mock_of[ConversationParticipantRepository]()
    repo.query.return_value = ()
    await _service(participant_repo=repo).active_participants(sid("conv-1"))
    repo.query.assert_awaited_once_with(
        ConversationParticipantFilterSpec(
            conversation_id=sid("conv-1"),
            status=ConversationParticipantStatus.ACTIVE,
        ),
    )


async def test_add_participant_saves_row() -> None:
    repo = mock_of[ConversationParticipantRepository]()
    participant = mock_of[ConversationParticipant]()
    await _service(participant_repo=repo).add_participant(participant)
    repo.save.assert_awaited_once_with(participant)


def _conversation(conversation_id: str, *, created_by: str) -> Conversation:
    """Build one conversation header owned by *created_by*.

    Returns:
        The header the opening-turn read is scoped against.
    """
    now = datetime(2026, 8, 19, tzinfo=UTC)
    return Conversation(
        id=as_uuid(conversation_id),
        created_by=NotBlankStr(created_by),
        created_at=now,
        updated_at=now,
    )


async def test_opening_turns_reads_the_whole_page_in_one_query() -> None:
    # The drawer names every row from its own opening sentence, so a per-row
    # read would put the page's cost on how many conversations there are.
    repo = mock_of[ConversationTurnRepository]()
    repo.query.return_value = ()

    await _service(turn_repo=repo).opening_turns(
        [
            _conversation("conv-a", created_by="owner-1"),
            _conversation("conv-b", created_by="owner-1"),
        ],
        created_by=NotBlankStr("owner-1"),
    )

    repo.query.assert_awaited_once_with(
        ConversationTurnFilterSpec(
            conversation_ids=(sid("conv-a"), sid("conv-b")),
            sequence=0,
        ),
        limit=2,
    )


async def test_opening_turns_reads_nothing_for_somebody_elses_conversation() -> None:
    # The turn carries what a person typed and the header is the only row that
    # says whose it is, so the check lives where the answer is rather than in
    # whichever caller assembled the ids.
    repo = mock_of[ConversationTurnRepository]()
    repo.query.return_value = ()

    opened = await _service(turn_repo=repo).opening_turns(
        [_conversation("conv-a", created_by="somebody-else")],
        created_by=NotBlankStr("owner-1"),
    )

    assert opened == {}
    repo.query.assert_not_awaited()


async def test_opening_turns_reads_nothing_for_an_empty_page() -> None:
    repo = mock_of[ConversationTurnRepository]()
    repo.query.return_value = ()

    opened = await _service(turn_repo=repo).opening_turns(
        [], created_by=NotBlankStr("owner-1")
    )

    assert opened == {}
    repo.query.assert_not_awaited()
