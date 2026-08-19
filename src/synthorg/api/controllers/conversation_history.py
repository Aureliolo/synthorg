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

from collections.abc import Mapping
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
from synthorg.api.state import AppState
from synthorg.core.actor_context import require_actor
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.conversation_title import derive_conversation_title
from synthorg.meta.chief_of_staff.models import Conversation, ConversationTurn
from synthorg.meta.chief_of_staff.resume_service import ConversationalResumeService
from synthorg.meta.errors import ConversationNotFoundError
from synthorg.meta.state import MetaStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.meta import (
    META_CHAT_CONVERSATION_ACCESS_DENIED,
    META_CHAT_DEPENDENCY_UNAVAILABLE,
)

logger = get_logger(__name__)

_DEFAULT_PAGE_SIZE: Final[int] = 50


def _require_resume_service(app_state: AppState) -> ConversationalResumeService:
    """Resolve the conversational resume facade, or 503.

    Returns:
        The wired resume service.

    Raises:
        ServiceUnavailableError: When persistence cannot back the
            conversation stores, so the drawer cannot list or resume.
    """
    service = app_state.slice(MetaStateSlice).conversational_resume_service
    if service is None:
        logger.warning(
            META_CHAT_DEPENDENCY_UNAVAILABLE,
            dependency="conversational_resume_service",
            hint="Conversational persistence must be wired during startup.",
        )
        msg = (
            "Conversation history is not configured; the durable "
            "conversation stores are unavailable."
        )
        raise ServiceUnavailableError(msg)
    return service


def _conversation_to_dict(
    conversation: Conversation,
    openings: Mapping[str, ConversationTurn],
) -> dict[str, object]:
    """Serialise a conversation header for the list endpoint.

    The title is resolved here, beside the row, rather than by the browser:
    the drawer would otherwise have to fetch a turn per row to find out what
    each one is called, and every row would read as its bare kind until those
    landed.

    Args:
        conversation: The header being serialised.
        openings: The page's opening turns, keyed by conversation id.

    Returns:
        A JSON-serialisable conversation summary. ``title`` is ``None`` when
        nothing names this conversation, and the client falls back to the kind
        label it already renders.
    """
    opening = openings.get(str(conversation.id))
    return {
        "id": str(conversation.id),
        "created_by": conversation.created_by,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
        "status": conversation.status.value,
        "kind": conversation.kind.value,
        "title": (
            None if opening is None else derive_conversation_title(opening.content)
        ),
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
            Paginated conversation summaries scoped to the caller.

        Raises:
            ServiceUnavailableError: When the durable conversation
                stores are not configured (503, not an empty 200 that a
                caller could misread as "no history").
        """
        app_state = state.app_state
        actor = require_actor()
        service = _require_resume_service(app_state)
        secret = cursor_secret_of(app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        conversations = await service.owner_conversations(
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
        rows = conversations[:limit]
        openings = await service.opening_turns(
            rows, created_by=NotBlankStr(actor.actor_id)
        )
        page = tuple(_conversation_to_dict(c, openings) for c in rows)
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
            ServiceUnavailableError: When the durable conversation
                stores are not configured (503).
            ConversationNotFoundError: When the conversation does not
                exist or is not the caller's (404 either way).
        """
        app_state = state.app_state
        actor = require_actor()
        service = _require_resume_service(app_state)
        conversation = await service.get_conversation(NotBlankStr(conversation_id))
        if conversation is None or conversation.created_by != actor.actor_id:
            logger.warning(
                META_CHAT_CONVERSATION_ACCESS_DENIED,
                conversation_id=conversation_id,
                reason="missing" if conversation is None else "not_owner",
            )
            raise ConversationNotFoundError(conversation_id=conversation_id)
        secret = cursor_secret_of(app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        turns = await service.conversation_turns(
            conversation_id=NotBlankStr(conversation_id),
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
