"""Message controller -- read + operator-driven DELETE via MessageService."""

from typing import Annotated, Final

from litestar import Controller, Request, delete, get
from litestar.datastructures import State
from litestar.params import QueryParameter

from synthorg._core.features import require_service
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.communication.channel import Channel
from synthorg.communication.message import Message
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.domain_errors import ResourceNotFoundError
from synthorg.core.pagination import collect_all
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.communication import (
    COMMUNICATION_MESSAGE_DELETE_FAILED,
)
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


class MessageController(Controller):
    """Access to message history (read + operator-driven delete)."""

    path = "/messages"
    tags = ("messages",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_messages(
        self,
        state: State,
        channel: Annotated[
            str | None,
            QueryParameter(
                max_length=QUERY_MAX_LENGTH,
                description="Filter to messages on this channel.",
            ),
        ] = None,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
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
            repo = persistence_of(app_state).messages
            channel_id = NotBlankStr(channel)
            messages = await collect_all(
                lambda fetch_limit, fetch_offset: repo.get_history(
                    channel_id,
                    limit=fetch_limit,
                    offset=fetch_offset,
                ),
            )
        else:
            messages = ()
        page, meta = paginate_cursor(
            messages,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        return PaginatedResponse(data=page, pagination=meta)

    @delete(
        "/{message_id:str}",
        status_code=200,
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("messages.delete", key="user"),
        ],
    )
    async def delete_message(
        self,
        state: State,
        request: Request[AuthenticatedUser, object, State],
        message_id: PathId,
    ) -> ApiResponse[None]:
        """Delete a single message by id.

        Returns ``200 OK`` with ``data=None`` on success and
        ``404 Not Found`` when the id does not exist. Routes through
        :class:`MessageService` so the audit-grade
        ``COMMUNICATION_MESSAGE_DELETED`` event is emitted exactly
        once from the service layer, on parity with the parallel MCP
        path (``synthorg_messages_delete``).

        Args:
            state: Litestar app state.
            request: Authenticated request; ``request.user.user_id``
                drives the audit log's actor field.
            message_id: Globally unique message identifier (the
                lookup key on the messages table).

        Returns:
            ``ApiResponse[None]`` instance.

        Raises:
            ResourceNotFoundError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        message_service = require_service(
            app_state.slice(CommunicationStateSlice).message_service,
            "Message Service",
        )
        deleted = await message_service.delete_message(
            message_id=message_id,
            actor_id=NotBlankStr(str(request.user.user_id)),
            reason=NotBlankStr("operator delete via REST API"),
        )
        if not deleted:
            logger.warning(
                COMMUNICATION_MESSAGE_DELETE_FAILED,
                message_id=message_id,
                actor_id=str(request.user.user_id),
                reason="not_found",
            )
            # Raising ``ResourceNotFoundError`` routes through
            # ``handle_domain_error`` so the response body carries
            # the structured RFC 9457 envelope every other 404 in
            # the API uses; ``litestar.NotFoundException`` would
            # bypass ``handle_domain_error`` and lose the category
            # / error_code triple.
            msg = f"message {message_id!r} not found"
            raise ResourceNotFoundError(msg)
        return ApiResponse(data=None)

    @get("/channels")
    async def list_channels(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
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
        message_bus = require_service(
            app_state.slice(CommunicationStateSlice).message_bus, "Message Bus"
        )
        channels = await message_bus.list_channels()
        page, meta = paginate_cursor(
            tuple(channels),
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        return PaginatedResponse[Channel](data=page, pagination=meta)
