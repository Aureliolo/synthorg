# module-kind: controller
"""Read endpoints for resuming a prior conversational-org conversation.

The four mutating chat endpoints hold no client-side transcript (the
dashboard is a pure API consumer), so a conversation that scrolled off or
survived a reload needs a server-side way back in. These two read
endpoints list the caller's own conversations and page their turns, both
cursor-paginated and strictly owner-scoped: a foreign or unknown id 404s
identically so a caller can never distinguish "not mine" from "does not
exist".
"""

from typing import Final

from litestar import Controller, get
from litestar.datastructures import State

from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    encode_countless_seek_meta,
)
from synthorg.api.path_params import PathId
from synthorg.core.actor_context import require_actor
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.models import Conversation, ConversationTurn
from synthorg.meta.errors import ConversationNotFoundError
from synthorg.persistence.conversation_protocol import ConversationTurnFilterSpec
from synthorg.persistence.conversational_factory import (
    build_conversational_repositories,
)
from synthorg.persistence.state import persistence_of

_DEFAULT_PAGE_SIZE: Final[int] = 50


def _conversation_to_dict(conversation: Conversation) -> dict[str, object]:
    """Serialise a conversation header for the list endpoint.

    Returns:
        A JSON-serialisable conversation summary.
    """
    return {
        "id": str(conversation.id),
        "created_by": conversation.created_by,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
        "status": conversation.status.value,
        "kind": conversation.kind.value,
    }


def _turn_to_dict(turn: ConversationTurn) -> dict[str, object]:
    """Serialise one conversation turn for the resume endpoint.

    Returns:
        A JSON-serialisable turn record.
    """
    return {
        "id": str(turn.id),
        "conversation_id": turn.conversation_id,
        "sequence": turn.sequence,
        "role": turn.role.value,
        "content": turn.content,
        "author_agent_id": turn.author_agent_id,
        "author_name": turn.author_name,
        "routed_topic": turn.routed_topic,
        "routing_confidence": turn.routing_confidence,
        "created_at": turn.created_at.isoformat(),
    }


class ConversationHistoryController(Controller):
    """List + resume the caller's own conversational-org conversations."""

    path = "/meta/chat/conversations"
    tags = ["meta-chat"]  # noqa: RUF012
    guards = [require_read_access]  # noqa: RUF012

    @get("/")
    async def list_conversations(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_PAGE_SIZE,
    ) -> PaginatedResponse[dict[str, object]]:
        """List the caller's conversations, newest-first, paginated.

        Args:
            state: Application state.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated conversation summaries scoped to the caller; an
            empty page when the durable stores are unavailable.
        """
        app_state = state.app_state
        actor = require_actor()
        secret = cursor_secret_of(app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        repos = build_conversational_repositories(persistence_of(app_state))
        conversations: tuple[Conversation, ...] = ()
        if repos is not None:
            conversations = await repos.conversation_repo.list_items(
                created_by=NotBlankStr(actor.actor_id),
                limit=limit + 1,
                offset=offset,
            )
        meta = encode_countless_seek_meta(
            offset=offset,
            fetched_rows=len(conversations),
            limit=limit,
            secret=secret,
        )
        page = tuple(_conversation_to_dict(c) for c in conversations[:limit])
        return PaginatedResponse[dict[str, object]](data=page, pagination=meta)

    @get("/{conversation_id:str}")
    async def get_conversation(
        self,
        conversation_id: PathId,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_PAGE_SIZE,
    ) -> PaginatedResponse[dict[str, object]]:
        """Page one conversation's turns, newest-first, owner-scoped.

        Args:
            conversation_id: The conversation to resume.
            state: Application state.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated turns for the conversation.

        Raises:
            ConversationNotFoundError: When the conversation does not
                exist or is not the caller's (404 either way).
        """
        app_state = state.app_state
        actor = require_actor()
        repos = build_conversational_repositories(persistence_of(app_state))
        if repos is None:
            raise ConversationNotFoundError(conversation_id=conversation_id)
        conversation = await repos.conversation_repo.get(NotBlankStr(conversation_id))
        if conversation is None or conversation.created_by != actor.actor_id:
            raise ConversationNotFoundError(conversation_id=conversation_id)
        secret = cursor_secret_of(app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        turns = await repos.turn_repo.query(
            ConversationTurnFilterSpec(conversation_id=NotBlankStr(conversation_id)),
            limit=limit + 1,
            offset=offset,
        )
        meta = encode_countless_seek_meta(
            offset=offset,
            fetched_rows=len(turns),
            limit=limit,
            secret=secret,
        )
        page = tuple(_turn_to_dict(t) for t in turns[:limit])
        return PaginatedResponse[dict[str, object]](data=page, pagination=meta)


__all__ = ["ConversationHistoryController"]
