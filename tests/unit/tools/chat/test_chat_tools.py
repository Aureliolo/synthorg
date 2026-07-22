"""Unit tests for the resource-grouped chat agent tools.

The chat Web API client is faked (via patching ``build_chat_api_client``)
so these tool-layer tests stay vendor-neutral and isolate the tool logic
(resolve / gate / dispatch / format) from the platform client, which is
covered by the ``chat_api`` client tests.
"""

import json
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.approval.enums import ApprovalStatus
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.core.types import NotBlankStr
from synthorg.integrations.chat_api.protocol import (
    ChatApiClient,
    ChatChannel,
    ChatMessage,
    ChatMessageRef,
    ChatUser,
)
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionType,
)
from synthorg.integrations.errors import (
    ChatApiAuthError,
    ChatApiError,
    ChatApiRateLimitError,
    SecretRetrievalError,
)
from synthorg.tools._governed_action import (
    ConnectionApprovalGate,
    GovernedApprovalMismatchError,
)
from synthorg.tools.chat._runtime import ChatToolDeps, ChatToolsRuntime
from synthorg.tools.chat.chat_tools import ChatDirectoryTool, ChatMessagesTool
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.unit

_COMMS_EXTERNAL = "comms:external"
_PATCH_TARGET = "synthorg.tools.chat.chat_tools.build_chat_api_client"
_GATE_BUILDER_TARGET = "synthorg.tools._governed_connection_tool.build_connection_gate"


class _FakeChatClient:
    """In-memory ``ChatApiClient`` double recording the calls it receives."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str | None]] = []
        self.aclosed = False

    async def send_message(
        self, *, channel: NotBlankStr, text: NotBlankStr, thread_ts: str | None = None
    ) -> ChatMessageRef:
        self.sent.append((str(channel), str(text), thread_ts))
        return ChatMessageRef(channel=str(channel), ts="1.0")

    async def read_channel(
        self, *, channel: NotBlankStr, limit: int
    ) -> tuple[ChatMessage, ...]:
        _ = (channel, limit)
        return (ChatMessage(ts="2", author="U1", text="hi"),)

    async def read_thread(
        self, *, channel: NotBlankStr, thread_ts: NotBlankStr, limit: int
    ) -> tuple[ChatMessage, ...]:
        _ = (channel, thread_ts, limit)
        return (ChatMessage(ts="1", author="U1", text="root", thread_ts="1"),)

    async def list_channels(self, *, limit: int) -> tuple[ChatChannel, ...]:
        _ = limit
        return (ChatChannel(id=NotBlankStr("C1"), name="general", is_member=True),)

    async def lookup_user(
        self, *, user_id: str | None = None, email: str | None = None
    ) -> ChatUser:
        _ = (user_id, email)
        return ChatUser(id=NotBlankStr("U1"), name="alice", email="a@example.com")

    async def aclose(self) -> None:
        self.aclosed = True


def _connection(*, ctype: ConnectionType = ConnectionType.SLACK) -> Connection:
    return Connection(
        name="chat",
        connection_type=ctype,
        auth_method=AuthMethod.BEARER_TOKEN,
        base_url=None,
    )


def _deps(
    *,
    conn: Connection | None,
    store: ApprovalStore | None = None,
    autonomy: EffectiveAutonomy | None = None,
    credentials: dict[str, str] | None = None,
    credentials_error: Exception | None = None,
) -> ChatToolDeps:
    get_credentials = AsyncMock(spec=ConnectionCatalog.get_credentials)
    if credentials_error is not None:
        get_credentials.side_effect = credentials_error
    else:
        get_credentials.return_value = credentials or {"token": "xoxb-t"}
    catalog = mock_of[ConnectionCatalog](
        get=AsyncMock(spec=ConnectionCatalog.get, return_value=conn),
        get_credentials=get_credentials,
    )
    runtime = ChatToolsRuntime(
        connection_catalog=catalog, connection_name="chat", timeout_seconds=5.0
    )
    return ChatToolDeps(
        runtime=runtime,
        approval_store=store or ApprovalStore(),
        agent_id="agent-1",
        task_id="task-1",
        effective_autonomy=autonomy,
    )


def _auto_autonomy() -> EffectiveAutonomy:
    return EffectiveAutonomy(
        level=AutonomyLevel.FULL,
        auto_approve_actions=frozenset({_COMMS_EXTERNAL}),
        human_approval_actions=frozenset(),
        security_agent=False,
    )


class TestChatMessagesApprovalFlow:
    async def test_send_parks_then_consumes_on_approval(self) -> None:
        fake = _FakeChatClient()
        store = ApprovalStore()
        send_args = {
            "action": "send",
            "channel": "C1",
            "text": "hello",
        }
        with patch(_PATCH_TARGET, return_value=fake):
            parked = await ChatMessagesTool(
                deps=_deps(conn=_connection(), store=store)
            ).execute(arguments=dict(send_args))
            assert parked.metadata["requires_parking"] is True
            assert fake.sent == []  # no egress on a parked write

            approval_id = cast("str", parked.metadata["approval_id"])
            item = await store.get(approval_id)
            assert item is not None
            await store.save(
                item.model_copy(update={"status": ApprovalStatus.APPROVED})
            )

            resumed = await ChatMessagesTool(
                deps=_deps(conn=_connection(), store=store)
            ).execute(arguments=dict(send_args))
        assert resumed.is_error is False
        assert fake.sent == [("C1", "hello", None)]

    async def test_auto_approved_send_skips_parking(self) -> None:
        fake = _FakeChatClient()
        with patch(_PATCH_TARGET, return_value=fake):
            tool = ChatMessagesTool(
                deps=_deps(conn=_connection(), autonomy=_auto_autonomy())
            )
            result = await tool.execute(
                arguments={"action": "send", "channel": "C1", "text": "hi"}
            )
        assert result.is_error is False
        assert fake.sent == [("C1", "hi", None)]

    async def test_read_channel_never_parks(self) -> None:
        fake = _FakeChatClient()
        with patch(_PATCH_TARGET, return_value=fake):
            tool = ChatMessagesTool(deps=_deps(conn=_connection()))
            result = await tool.execute(
                arguments={"action": "read_channel", "channel": "C1"}
            )
        assert result.is_error is False
        assert "requires_parking" not in result.metadata
        assert json.loads(result.content)[0]["text"] == "hi"


class TestChatDirectoryTool:
    async def test_list_channels(self) -> None:
        fake = _FakeChatClient()
        with patch(_PATCH_TARGET, return_value=fake):
            tool = ChatDirectoryTool(deps=_deps(conn=_connection()))
            result = await tool.execute(arguments={"action": "list_channels"})
        assert json.loads(result.content)[0]["id"] == "C1"

    async def test_lookup_user_by_id(self) -> None:
        fake = _FakeChatClient()
        with patch(_PATCH_TARGET, return_value=fake):
            tool = ChatDirectoryTool(deps=_deps(conn=_connection()))
            result = await tool.execute(
                arguments={"action": "lookup_user", "user_id": "U1"}
            )
        assert json.loads(result.content)["email"] == "a@example.com"

    async def test_lookup_user_rejects_both(self) -> None:
        tool = ChatDirectoryTool(deps=_deps(conn=_connection()))
        result = await tool.execute(
            arguments={
                "action": "lookup_user",
                "user_id": "U1",
                "email": "a@example.com",
            }
        )
        assert result.is_error is True
        assert "invalid arguments" in result.content.lower()

    async def test_lookup_user_rejects_neither(self) -> None:
        tool = ChatDirectoryTool(deps=_deps(conn=_connection()))
        result = await tool.execute(arguments={"action": "lookup_user"})
        assert result.is_error is True
        assert "invalid arguments" in result.content.lower()


class TestChatToolGuards:
    async def test_connection_not_found(self) -> None:
        tool = ChatDirectoryTool(deps=_deps(conn=None))
        result = await tool.execute(arguments={"action": "list_channels"})
        assert result.is_error is True
        assert "not found" in result.content.lower()

    async def test_unsupported_connection_type(self) -> None:
        tool = ChatDirectoryTool(
            deps=_deps(conn=_connection(ctype=ConnectionType.GITHUB))
        )
        result = await tool.execute(arguments={"action": "list_channels"})
        assert result.is_error is True

    async def test_rate_limit_maps_to_error_result(self) -> None:
        fake = _FakeChatClient()
        fake.list_channels = AsyncMock(  # type: ignore[method-assign]
            spec=ChatApiClient.list_channels,
            side_effect=ChatApiRateLimitError("slow down"),
        )
        with patch(_PATCH_TARGET, return_value=fake):
            tool = ChatDirectoryTool(deps=_deps(conn=_connection()))
            result = await tool.execute(arguments={"action": "list_channels"})
        assert result.is_error is True
        assert "rate" in result.content.lower()


class TestChatToolErrorMapping:
    async def test_credential_retrieval_failure_no_egress(self) -> None:
        fake = _FakeChatClient()
        with patch(_PATCH_TARGET, return_value=fake):
            tool = ChatDirectoryTool(
                deps=_deps(
                    conn=_connection(),
                    credentials_error=SecretRetrievalError("backend down"),
                )
            )
            result = await tool.execute(arguments={"action": "list_channels"})
        assert result.is_error is True
        assert fake.aclosed is False  # client never built

    async def test_missing_token_errors(self) -> None:
        fake = _FakeChatClient()
        with patch(_PATCH_TARGET, return_value=fake):
            tool = ChatDirectoryTool(
                deps=_deps(conn=_connection(), credentials={"nope": "x"})
            )
            result = await tool.execute(arguments={"action": "list_channels"})
        assert result.is_error is True
        assert "token" in result.content.lower()

    async def test_auth_error_maps_to_upstream_error(self) -> None:
        fake = _FakeChatClient()
        fake.list_channels = AsyncMock(  # type: ignore[method-assign]
            spec=ChatApiClient.list_channels,
            side_effect=ChatApiAuthError("bad token"),
        )
        with patch(_PATCH_TARGET, return_value=fake):
            tool = ChatDirectoryTool(deps=_deps(conn=_connection()))
            result = await tool.execute(arguments={"action": "list_channels"})
        assert result.is_error is True
        assert fake.aclosed is True  # aclose ran in finally despite the error

    async def test_bad_base_url_maps_to_argument_error(self) -> None:
        with patch(_PATCH_TARGET, side_effect=ChatApiError("egress not pinned")):
            tool = ChatDirectoryTool(deps=_deps(conn=_connection()))
            result = await tool.execute(arguments={"action": "list_channels"})
        assert result.is_error is True

    async def test_rate_limit_surfaces_retry_after_metadata(self) -> None:
        fake = _FakeChatClient()
        fake.list_channels = AsyncMock(  # type: ignore[method-assign]
            spec=ChatApiClient.list_channels,
            side_effect=ChatApiRateLimitError("slow down", retry_after_seconds=12.0),
        )
        with patch(_PATCH_TARGET, return_value=fake):
            tool = ChatDirectoryTool(deps=_deps(conn=_connection()))
            result = await tool.execute(arguments={"action": "list_channels"})
        assert result.is_error is True
        assert result.metadata["retry_after_seconds"] == 12.0

    async def test_aclose_runs_in_finally_on_success(self) -> None:
        fake = _FakeChatClient()
        with patch(_PATCH_TARGET, return_value=fake):
            tool = ChatDirectoryTool(deps=_deps(conn=_connection()))
            result = await tool.execute(arguments={"action": "list_channels"})
        assert result.is_error is False
        assert fake.aclosed is True

    async def test_approval_mismatch_maps_to_error_result(self) -> None:
        fake = _FakeChatClient()
        tool = ChatMessagesTool(deps=_deps(conn=_connection()))
        # A mismatched / consumed grant raises through the gate; execute()
        # must surface it as a structured error, not a generic invoker crash.
        gate = mock_of[ConnectionApprovalGate](
            gate=AsyncMock(
                spec=ConnectionApprovalGate.gate,
                side_effect=GovernedApprovalMismatchError("already consumed"),
            )
        )
        with (
            patch(_GATE_BUILDER_TARGET, return_value=gate),
            patch(_PATCH_TARGET, return_value=fake),
        ):
            result = await tool.execute(
                arguments={"action": "send", "channel": "C1", "text": "hi"}
            )
        assert result.is_error is True
        assert fake.sent == []  # no egress
