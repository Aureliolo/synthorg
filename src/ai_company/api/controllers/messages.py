"""Message controller — read-only access via MessageRepository."""

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002

from ai_company.api.dto import ApiResponse, PaginatedResponse
from ai_company.api.pagination import paginate
from ai_company.api.state import AppState  # noqa: TC001
from ai_company.communication.channel import Channel  # noqa: TC001
from ai_company.communication.message import Message  # noqa: TC001


class MessageController(Controller):
    """Read-only access to message history."""

    path = "/messages"
    tags = ("messages",)

    @get()
    async def list_messages(
        self,
        state: State,
        channel: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> PaginatedResponse[Message]:
        """List messages, optionally filtered by channel.

        Args:
            state: Application state.
            channel: Filter by channel name.
            offset: Pagination offset.
            limit: Page size.

        Returns:
            Paginated message list.
        """
        app_state: AppState = state.app_state
        if channel is not None:
            messages = await app_state.persistence.messages.get_history(
                channel,
                limit=None,
            )
        else:
            messages = ()
        page, meta = paginate(messages, offset=offset, limit=limit)
        return PaginatedResponse(data=page, pagination=meta)

    @get("/channels")
    async def list_channels(
        self,
        state: State,
    ) -> ApiResponse[tuple[Channel, ...]]:
        """List available message bus channels.

        Args:
            state: Application state.

        Returns:
            Channel list envelope.
        """
        app_state: AppState = state.app_state
        channels = await app_state.message_bus.list_channels()
        return ApiResponse(data=channels)
