"""Resource-grouped chat agent tools.

Vendor-neutral tools (``chat_messages`` / ``chat_directory``) that resolve a
bound chat connection, dispatch through the connection-type-keyed ``chat_api``
client registry, and route sending a message through the shared approval gate
(``COMMS_EXTERNAL``). The concrete platform is selected by the bound
connection's type, so the agent never depends on which platform the operator
connected. The shared resolve / gate / dispatch / error-map pipeline lives in
:mod:`synthorg.tools._governed_connection_tool`; this module supplies only the
chat-specific hooks and the per-resource dispatch.
"""

from abc import ABC
from typing import ClassVar, override

from pydantic import BaseModel

from synthorg.core.types import NotBlankStr
from synthorg.integrations.chat_api import (
    ChatApiClient,
    build_chat_api_client,
    chat_api_supported,
)
from synthorg.integrations.connections.models import Connection, ConnectionType
from synthorg.integrations.errors import (
    ChatApiAuthError,
    ChatApiError,
    ChatApiRateLimitError,
)
from synthorg.observability import safe_error_description
from synthorg.observability.events.tool import (
    CHAT_TOOL_CONNECTION_FAILED,
    CHAT_TOOL_CREDENTIAL_FAILED,
)
from synthorg.tools._governed_connection_tool import GovernedConnectionTool
from synthorg.tools._governed_connection_tool import json_result as _json_result
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.chat._args import ChatDirectoryArgs, ChatMessagesArgs
from synthorg.tools.chat._runtime import ChatToolDeps, ChatToolsRuntime
from synthorg.tools.chat.errors import (
    ChatConnectionNotFoundError,
    ChatCredentialError,
    ChatRateLimitedError,
    ChatToolArgumentError,
    ChatUnsupportedError,
    ChatUpstreamError,
)
from synthorg.tools.errors import ToolError


class _BaseChatTool(GovernedConnectionTool[ChatApiClient, ChatToolsRuntime], ABC):
    """Chat bindings for the shared governed-connection tool pipeline."""

    _KIND: ClassVar[str] = "Chat"
    _CONNECTION_FAILED_EVENT: ClassVar[str] = CHAT_TOOL_CONNECTION_FAILED
    _CREDENTIAL_FAILED_EVENT: ClassVar[str] = CHAT_TOOL_CREDENTIAL_FAILED
    # A chat platform (e.g. Slack) has a default host, so an empty base_url
    # is valid; the client factory pins egress to the platform's host.
    _REQUIRE_BASE_URL: ClassVar[bool] = False
    _UNSUPPORTED_MSG: ClassVar[str] = (
        "Connection type {ctype!r} has no chat Web API client wired"
    )
    _UNSUPPORTED_REASON: ClassVar[str] = "unsupported_platform"
    _not_found_error: ClassVar[type[ToolError]] = ChatConnectionNotFoundError
    _unsupported_error: ClassVar[type[ToolError]] = ChatUnsupportedError
    _argument_error: ClassVar[type[ToolError]] = ChatToolArgumentError
    _credential_error: ClassVar[type[ToolError]] = ChatCredentialError
    _rate_limited_error: ClassVar[type[ToolError]] = ChatRateLimitedError

    def __init__(
        self,
        *,
        name: str,
        description: str,
        args_model: type[BaseModel],
        deps: ChatToolDeps,
    ) -> None:
        super().__init__(
            name=name,
            description=description,
            args_model=args_model,
            runtime=deps.runtime,
            gate_deps=deps,
        )

    @override
    def _supported(self, connection_type: ConnectionType) -> bool:
        return chat_api_supported(connection_type)

    @override
    def _build_client(
        self,
        *,
        conn: Connection,
        token: str,
        timeout: float,
    ) -> ChatApiClient:
        try:
            return build_chat_api_client(
                connection_type=conn.connection_type,
                base_url=str(conn.base_url or ""),
                token=token,
                timeout=timeout,
            )
        except ChatApiError as exc:
            raise ChatToolArgumentError(safe_error_description(exc)) from exc

    @override
    async def _dispatch_guarded(
        self, client: ChatApiClient, args: BaseModel
    ) -> ToolExecutionResult:
        """Dispatch and map lower-level chat-client errors to typed leaves.

        Returns:
            The tool result.

        Raises:
            ChatRateLimitedError: The chat platform rate-limited the request.
            ChatUpstreamError: An auth or other chat API / transport failure.
        """
        try:
            return await self._dispatch(client, args)
        except ChatApiRateLimitError as exc:
            msg = "Chat platform rate-limited the request; retry later"
            raise ChatRateLimitedError(
                msg, retry_after_seconds=exc.retry_after_seconds
            ) from exc
        except ChatApiAuthError as exc:
            msg = "Chat authentication failed (check the connection token/scopes)"
            raise ChatUpstreamError(msg) from exc
        except ChatApiError as exc:
            raise ChatUpstreamError(safe_error_description(exc)) from exc


class ChatMessagesTool(_BaseChatTool):
    """Send a message, read a channel, or read a thread."""

    args_model: ClassVar[type[BaseModel] | None] = ChatMessagesArgs

    def __init__(self, *, deps: ChatToolDeps) -> None:
        super().__init__(
            name="chat_messages",
            description=(
                "Work with chat messages on the bound connection: send a message"
                " (send, requires text; optional thread_ts), read a channel's recent"
                " messages (read_channel), or read a thread's replies (read_thread,"
                " requires thread_ts). Sending requires approval."
            ),
            args_model=ChatMessagesArgs,
            deps=deps,
        )

    @override
    async def _dispatch(
        self, client: ChatApiClient, args: BaseModel
    ) -> ToolExecutionResult:
        assert isinstance(args, ChatMessagesArgs)  # noqa: S101 -- parsed by execute
        channel = NotBlankStr(args.channel)
        if args.action == "send":
            ref = await client.send_message(
                channel=channel,
                text=NotBlankStr(args.text),
                thread_ts=args.thread_ts or None,
            )
            return _json_result(ref.model_dump(mode="json"))
        if args.action == "read_channel":
            messages = await client.read_channel(channel=channel, limit=args.limit)
            return _json_result([m.model_dump(mode="json") for m in messages])
        messages = await client.read_thread(
            channel=channel, thread_ts=NotBlankStr(args.thread_ts), limit=args.limit
        )
        return _json_result([m.model_dump(mode="json") for m in messages])


class ChatDirectoryTool(_BaseChatTool):
    """List channels or look up a user."""

    args_model: ClassVar[type[BaseModel] | None] = ChatDirectoryArgs

    def __init__(self, *, deps: ChatToolDeps) -> None:
        super().__init__(
            name="chat_directory",
            description=(
                "Look up chat workspace directory data on the bound connection: list"
                " channels the bot can see (list_channels) or look up a user by"
                " user_id or email (lookup_user, exactly one)."
            ),
            args_model=ChatDirectoryArgs,
            deps=deps,
        )

    @override
    async def _dispatch(
        self, client: ChatApiClient, args: BaseModel
    ) -> ToolExecutionResult:
        assert isinstance(args, ChatDirectoryArgs)  # noqa: S101 -- parsed by execute
        if args.action == "list_channels":
            channels = await client.list_channels(limit=args.limit)
            return _json_result([c.model_dump(mode="json") for c in channels])
        user = await client.lookup_user(
            user_id=args.user_id or None, email=args.email or None
        )
        return _json_result(user.model_dump(mode="json"))


__all__ = ["ChatDirectoryTool", "ChatMessagesTool"]
