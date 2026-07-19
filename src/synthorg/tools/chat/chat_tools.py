"""Resource-grouped chat agent tools.

Vendor-neutral tools (``chat_messages`` / ``chat_directory``) that
resolve a bound chat connection, dispatch through the
connection-type-keyed ``chat_api`` client registry, and route sending a
message through the shared approval gate (``COMMS_EXTERNAL``). Slack is
the first platform; other chat providers slot in by registering a client,
so the agent never depends on which platform the operator connected.
"""

import json
from abc import ABC, abstractmethod
from typing import ClassVar, override

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from synthorg.core.boundary import parse_typed
from synthorg.core.types import NotBlankStr
from synthorg.integrations.chat_api import (
    ChatApiClient,
    build_chat_api_client,
    chat_api_supported,
)
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import Connection
from synthorg.integrations.errors import (
    ChatApiAuthError,
    ChatApiError,
    ChatApiRateLimitError,
    SecretRetrievalError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.tool import CHAT_TOOL_CREDENTIAL_FAILED
from synthorg.security.autonomy.enums import ActionType, ToolCategory
from synthorg.tools._governed_action import ActionSignature, ConnectionApprovalGate
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.chat._args import ChatDirectoryArgs, ChatMessagesArgs
from synthorg.tools.chat._runtime import ChatToolDeps
from synthorg.tools.chat.errors import (
    ChatConnectionNotFoundError,
    ChatCredentialError,
    ChatRateLimitedError,
    ChatToolError,
    ChatUnsupportedError,
    ChatUpstreamError,
)

logger = get_logger(__name__)

_ACTION_TYPE = ActionType.COMMS_EXTERNAL.value


class _BaseChatTool(BaseTool, ABC):
    """Shared connection-resolution + approval gating for the chat tools."""

    args_model: ClassVar[type[BaseModel] | None] = None

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
            # EXTERNAL_DATA (not COMMUNICATION): a chat tool is external-API
            # access of the same shape as the forge / external_api tools
            # (bound connection, credential-brokered, approval-gated egress),
            # so it belongs to the same access tier. Governance is the
            # COMMS_EXTERNAL action_type plus the approval gate, not the
            # category.
            category=ToolCategory.EXTERNAL_DATA,
            action_type=_ACTION_TYPE,
            parameters_schema=args_model.model_json_schema(),
        )
        self._runtime = deps.runtime
        self._gate = ConnectionApprovalGate(
            approval_store=deps.approval_store,
            agent_id=deps.agent_id,
            task_id=deps.task_id,
            action_type=_ACTION_TYPE,
            effective_autonomy=deps.effective_autonomy,
            risk_classifier=deps.risk_classifier,
            clock=deps.clock,
        )

    @property
    def _catalog(self) -> ConnectionCatalog:
        return self._runtime.connection_catalog

    async def _run(self, args: BaseModel) -> ToolExecutionResult:
        """Resolve the connection, gate writes, then dispatch.

        Returns:
            The tool result, or an approval-parking result.

        Raises:
            ChatToolError: On any connection / credential / upstream
                failure (mapped to a typed leaf).
        """
        conn = await self._resolve_connection()
        if bool(getattr(args, "is_write", False)):
            parked = await self._gate.gate(
                _signature(self.name, self._runtime.connection_name, args),
                connection=self._runtime.connection_name,
                approval_id=None,
                title=f"Chat {self.name} on {self._runtime.connection_name!r}",
                description=f"Agent requests a chat {self.name} write.",
            )
            if parked is not None:
                return parked
        token = await self._resolve_token(conn)
        client = build_chat_api_client(
            connection_type=conn.connection_type,
            base_url=str(conn.base_url or ""),
            token=token,
            timeout=self._runtime.timeout_seconds,
        )
        try:
            return await self._dispatch_guarded(client, args)
        finally:
            await client.aclose()

    async def _resolve_connection(self) -> Connection:
        conn = await self._catalog.get(self._runtime.connection_name)
        if conn is None:
            msg = f"Chat connection {self._runtime.connection_name!r} not found"
            raise ChatConnectionNotFoundError(msg)
        if not chat_api_supported(conn.connection_type):
            msg = (
                f"Connection type {conn.connection_type.value!r} has no chat Web API"
                " client wired"
            )
            raise ChatUnsupportedError(msg)
        return conn

    async def _resolve_token(self, conn: Connection) -> str:
        try:
            credentials = await self._catalog.get_credentials(conn.name)
        except SecretRetrievalError as exc:
            logger.warning(
                CHAT_TOOL_CREDENTIAL_FAILED,
                connection=conn.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to broker credentials for chat connection"
            raise ChatCredentialError(msg) from exc
        token = credentials.get("token")
        if not token:
            msg = f"Chat connection {conn.name!r} has no token"
            raise ChatCredentialError(msg)
        return token

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
            raise ChatRateLimitedError(msg) from exc
        except ChatApiAuthError as exc:
            msg = "Chat authentication failed (check the connection token/scopes)"
            raise ChatUpstreamError(msg) from exc
        except ChatApiError as exc:
            raise ChatUpstreamError(safe_error_description(exc)) from exc

    @abstractmethod
    async def _dispatch(
        self, client: ChatApiClient, args: BaseModel
    ) -> ToolExecutionResult:
        """Map the parsed action onto a client call and format the result."""

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        """Run the chat tool.

        Returns:
            The tool result (or an approval-parking result).
        """
        model = self.args_model
        assert model is not None  # noqa: S101 -- set by every subclass
        try:
            args = parse_typed("tool.execute", arguments, model)
        except PydanticValidationError as exc:
            return ToolExecutionResult(
                content=f"Invalid arguments: {safe_error_description(exc)}",
                is_error=True,
            )
        try:
            return await self._run(args)
        except ChatToolError as exc:
            return ToolExecutionResult(content=str(exc), is_error=True)


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


def _signature(tool_name: str, connection: str, args: BaseModel) -> ActionSignature:
    return ActionSignature.build(
        namespace=tool_name,
        connection=connection,
        operation=str(getattr(args, "action", "")),
        payload=args.model_dump(mode="json"),
    )


def _json_result(data: object) -> ToolExecutionResult:
    return ToolExecutionResult(content=json.dumps(data, ensure_ascii=False))


__all__ = ["ChatDirectoryTool", "ChatMessagesTool"]
