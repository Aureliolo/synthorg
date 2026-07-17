# module-kind: tests
"""Unit tests for the conversation-resume read controller.

Covers the two load-bearing guarantees: the durable stores being absent
degrades to a 503 (never a misleading empty 200), and the owner-scoping
that makes a foreign or unknown conversation id 404 identically, so a
caller can neither read nor probe another owner's conversations.
"""

from datetime import UTC, datetime

import pytest
from litestar.datastructures import State

from synthorg.api.controllers.conversation_history import (
    ConversationHistoryController,
    _require_resume_service,
)
from synthorg.api.cursor import CursorSecret
from synthorg.communication.conversation.enums import (
    ConversationRole,
    ConversationStatus,
)
from synthorg.core.actor_context import ActorIdentity, ActorKind, actor_scope
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.enums import ConversationKind
from synthorg.meta.chief_of_staff.models import Conversation, ConversationTurn
from synthorg.meta.chief_of_staff.resume_service import ConversationalResumeService
from synthorg.meta.errors import ConversationNotFoundError
from synthorg.meta.state import MetaStateSlice
from synthorg.persistence.conversation_invite_protocol import (
    ConversationInviteRepository,
)
from synthorg.persistence.conversation_participant_protocol import (
    ConversationParticipantRepository,
)
from tests._shared import make_app_state, mock_of
from tests._shared.conversation_fakes import FakeConversationRepo, FakeTurnRepo

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


def _human(actor_id: str) -> ActorIdentity:
    """Build a HUMAN actor with the given id.

    Returns:
        A frozen HUMAN :class:`ActorIdentity`.
    """
    return ActorIdentity(actor_id=NotBlankStr(actor_id), kind=ActorKind.HUMAN)


def _conversation(*, owner: str) -> Conversation:
    """Build an active direct conversation owned by *owner*.

    Returns:
        The conversation header.
    """
    return Conversation(
        created_by=NotBlankStr(owner),
        created_at=_NOW,
        updated_at=_NOW,
        status=ConversationStatus.ACTIVE,
        kind=ConversationKind.DIRECT,
    )


def _service(
    conv_repo: FakeConversationRepo, turn_repo: FakeTurnRepo
) -> ConversationalResumeService:
    """Wire a resume service over the two history repos (others stubbed).

    Returns:
        The resume service; only its conversation/turn reads are exercised.
    """
    return ConversationalResumeService(
        invite_repo=mock_of[ConversationInviteRepository](),
        participant_repo=mock_of[ConversationParticipantRepository](),
        conversation_repo=conv_repo,
        turn_repo=turn_repo,
    )


def _controller() -> ConversationHistoryController:
    """Instantiate the controller without Litestar's router wiring.

    Returns:
        A bare controller whose handler methods can be called directly.
    """
    return object.__new__(ConversationHistoryController)


# The ``@get`` decorator wraps each method in a route handler; ``.fn`` is
# the raw async function, callable directly with the controller as ``self``.
_get_conversation = ConversationHistoryController.get_conversation.fn
_list_conversations = ConversationHistoryController.list_conversations.fn


def _state(service: ConversationalResumeService | None) -> State:
    """Build a request State carrying an app_state with *service* wired.

    Returns:
        A Litestar State exposing ``.app_state``.
    """
    app_state = make_app_state(cursor_secret=CursorSecret.ephemeral())
    if service is not None:
        app_state.wire(MetaStateSlice, conversational_resume_service=service)
    return State({"app_state": app_state})


class TestRequireResumeService:
    """The 503 degrade when the durable stores are unavailable."""

    def test_missing_service_503s(self) -> None:
        with pytest.raises(ServiceUnavailableError):
            _require_resume_service(_state(None).app_state)


class TestGetConversationOwnerScoping:
    """A foreign or unknown id 404s; the owner reads their own turns."""

    async def test_foreign_owner_404s(self) -> None:
        conv_repo = FakeConversationRepo()
        conversation = _conversation(owner="bob")
        await conv_repo.save(conversation)
        state = _state(_service(conv_repo, FakeTurnRepo()))
        controller = _controller()
        with actor_scope(_human("alice")), pytest.raises(ConversationNotFoundError):
            await _get_conversation(
                controller,
                conversation_id=NotBlankStr(str(conversation.id)),
                state=state,
            )

    async def test_unknown_id_404s(self) -> None:
        state = _state(_service(FakeConversationRepo(), FakeTurnRepo()))
        controller = _controller()
        with actor_scope(_human("alice")), pytest.raises(ConversationNotFoundError):
            await _get_conversation(
                controller,
                conversation_id=NotBlankStr("does-not-exist"),
                state=state,
            )

    async def test_owner_reads_own_turns(self) -> None:
        conv_repo = FakeConversationRepo()
        turn_repo = FakeTurnRepo()
        conversation = _conversation(owner="alice")
        await conv_repo.save(conversation)
        await turn_repo.append(
            ConversationTurn(
                conversation_id=str(conversation.id),
                sequence=0,
                role=ConversationRole.USER,
                content=NotBlankStr("what is revenue?"),
                created_at=_NOW,
            )
        )
        state = _state(_service(conv_repo, turn_repo))
        controller = _controller()
        with actor_scope(_human("alice")):
            page = await _get_conversation(
                controller,
                conversation_id=NotBlankStr(str(conversation.id)),
                state=state,
            )
        assert len(page.data) == 1
        assert page.data[0]["content"] == "what is revenue?"


class TestListConversationsOwnerScoping:
    """The list is scoped to the caller; other owners' rows never leak."""

    async def test_lists_only_callers_conversations(self) -> None:
        conv_repo = FakeConversationRepo()
        await conv_repo.save(_conversation(owner="alice"))
        await conv_repo.save(_conversation(owner="bob"))
        state = _state(_service(conv_repo, FakeTurnRepo()))
        controller = _controller()
        with actor_scope(_human("alice")):
            page = await _list_conversations(controller, state=state)
        assert len(page.data) == 1
        assert page.data[0]["created_by"] == "alice"
