"""Message controller -- read + operator-driven DELETE via MessageRepository."""

from typing import Any

from litestar import Controller, Request, delete, get
from litestar.datastructures import State  # noqa: TC002
from litestar.exceptions import NotFoundException

from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import CursorLimit, CursorParam, paginate_cursor
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.communication.channel import Channel
from synthorg.communication.message import Message  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.communication import (
    COMMUNICATION_MESSAGE_DELETED,
)

logger = get_logger(__name__)


class MessageController(Controller):
    """Access to message history (read + operator-driven delete)."""

    path = "/messages"
    tags = ("messages",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_messages(
        self,
        state: State,
        channel: str | None = None,
        cursor: CursorParam = None,
        limit: CursorLimit = 50,
    ) -> PaginatedResponse[Message]:
        """List messages, optionally filtered by channel.

        When no ``channel`` filter is provided, returns an empty
        list -- use ``GET /messages/channels`` to discover available
        channels first.

        Args:
            state: Application state.
            channel: Filter by channel name.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated message list.
        """
        app_state: AppState = state.app_state
        if channel is not None:
            messages = await app_state.persistence.messages.get_history(
                channel,
            )
        else:
            messages = ()
        page, meta = paginate_cursor(
            messages,
            limit=limit,
            cursor=cursor,
            secret=app_state.cursor_secret,
        )
        return PaginatedResponse(data=page, pagination=meta)

    @delete(
        "/{message_id:str}",
        status_code=200,
        guards=[require_write_access],
    )
    async def delete_message(
        self,
        state: State,
        request: Request[Any, Any, Any],
        message_id: str,
        channel: str | None = None,
    ) -> ApiResponse[None]:
        """Delete a single message by id.

        Returns ``200 OK`` with ``data=None`` on success and
        ``404 Not Found`` when the id does not exist. Emits the
        ``COMMUNICATION_MESSAGE_DELETED`` audit event on success with
        the authenticated user as the actor; the parallel MCP path
        (``synthorg_messages_delete``) emits the same event from the
        :class:`MessageService` layer so both surfaces produce a
        uniform audit trail (same key set, with ``channel`` reported
        as ``None`` when the REST caller did not supply it).

        Goes directly through the repository (matching the read path
        in ``list_messages``) because :class:`MessageService` is wired
        only inside the MCP host today; the audit log is emitted
        inline so the HTTP path retains the same audit semantics
        regardless.

        Args:
            state: Litestar app state.
            request: Authenticated request; ``request.user.user_id``
                drives the audit log's actor field.
            message_id: Globally unique message identifier (the
                lookup key on the messages table).
            channel: Optional channel attribution for the audit log
                so callers can record which logical bus the message
                belonged to. ``None`` is acceptable since
                ``messages.id`` is globally unique; the value is
                advisory metadata, not a lookup key.
        """
        app_state: AppState = state.app_state
        deleted = await app_state.persistence.messages.delete(message_id)
        if not deleted:
            raise NotFoundException(detail=f"message {message_id!r} not found")
        logger.info(
            COMMUNICATION_MESSAGE_DELETED,
            channel=channel,
            message_id=message_id,
            actor_id=str(request.user.user_id),
            reason="operator delete via REST API",
        )
        return ApiResponse(data=None)

    @get("/channels")
    async def list_channels(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = 50,
    ) -> PaginatedResponse[Channel]:
        """List available message bus channels (paginated).

        Args:
            state: Application state.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated channel list envelope.
        """
        app_state: AppState = state.app_state
        channels = await app_state.message_bus.list_channels()
        page, meta = paginate_cursor(
            tuple(channels),
            limit=limit,
            cursor=cursor,
            secret=app_state.cursor_secret,
        )
        return PaginatedResponse[Channel](data=page, pagination=meta)
