"""Tests for the Web-API Slack notification sink.

The sink resolves a bound ``SLACK`` connection's bot token lazily on the
first send and posts via ``chat.postMessage``. The chat client is faked
(via patching ``build_chat_api_client``) so these tests stay vendor-
neutral and offline.
"""

from unittest.mock import AsyncMock, patch

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.integrations.chat_api.protocol import (
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
from synthorg.integrations.errors import ChatApiError, SecretRetrievalError
from synthorg.notifications.adapters.slack import SlackNotificationSink, _format_message
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.unit

_PATCH_TARGET = "synthorg.notifications.adapters.slack.build_chat_api_client"


class _FakeChatClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.aclosed = False

    async def send_message(
        self, *, channel: NotBlankStr, text: NotBlankStr, thread_ts: str | None = None
    ) -> ChatMessageRef:
        _ = thread_ts
        self.sent.append((str(channel), str(text)))
        return ChatMessageRef(channel=str(channel), ts="1.0")

    async def read_channel(
        self, *, channel: NotBlankStr, limit: int
    ) -> tuple[ChatMessage, ...]:
        _ = (channel, limit)
        return ()

    async def read_thread(
        self, *, channel: NotBlankStr, thread_ts: NotBlankStr, limit: int
    ) -> tuple[ChatMessage, ...]:
        _ = (channel, thread_ts, limit)
        return ()

    async def list_channels(self, *, limit: int) -> tuple[ChatChannel, ...]:
        _ = limit
        return ()

    async def lookup_user(
        self, *, user_id: str | None = None, email: str | None = None
    ) -> ChatUser:
        _ = (user_id, email)
        return ChatUser(id="U1", name="bot")

    async def aclose(self) -> None:
        self.aclosed = True


def _connection() -> Connection:
    return Connection(
        name="slack-conn",
        connection_type=ConnectionType.SLACK,
        auth_method=AuthMethod.BEARER_TOKEN,
        base_url=None,
    )


def _catalog(
    *,
    conn: Connection | None,
    credentials: dict[str, str] | None = None,
    credentials_error: Exception | None = None,
) -> ConnectionCatalog:
    get_credentials = AsyncMock(spec=ConnectionCatalog.get_credentials)
    if credentials_error is not None:
        get_credentials.side_effect = credentials_error
    else:
        get_credentials.return_value = (
            credentials if credentials is not None else {"token": "xoxb"}
        )
    catalog: ConnectionCatalog = mock_of[ConnectionCatalog](
        get=AsyncMock(spec=ConnectionCatalog.get, return_value=conn),
        get_credentials=get_credentials,
    )
    return catalog


def _sink(catalog: ConnectionCatalog) -> SlackNotificationSink:
    return SlackNotificationSink(
        connection_catalog=catalog, connection_name="slack-conn", channel="C1"
    )


def _notification() -> Notification:
    return Notification(
        category=NotificationCategory.SYSTEM,
        severity=NotificationSeverity.WARNING,
        title="Disk full",
        body="90% used",
        source="monitor",
    )


class TestSlackSink:
    def test_constructor_does_not_build_client(self) -> None:
        sink = _sink(_catalog(conn=_connection()))
        assert sink._client is None

    async def test_send_posts_formatted_message(self) -> None:
        fake = _FakeChatClient()
        sink = _sink(_catalog(conn=_connection()))
        with patch(_PATCH_TARGET, return_value=fake):
            await sink.send(_notification())
        assert len(fake.sent) == 1
        channel, text = fake.sent[0]
        assert channel == "C1"
        assert "*[WARNING]* Disk full" in text
        assert "Category: system | Source: monitor" in text

    async def test_client_built_once_and_reused(self) -> None:
        fake = _FakeChatClient()
        sink = _sink(_catalog(conn=_connection()))
        with patch(_PATCH_TARGET, return_value=fake) as build:
            await sink.send(_notification())
            await sink.send(_notification())
        assert build.call_count == 1
        assert len(fake.sent) == 2

    async def test_missing_connection_is_a_no_op(self) -> None:
        sink = _sink(_catalog(conn=None))
        with patch(_PATCH_TARGET) as build:
            await sink.send(_notification())
        build.assert_not_called()
        assert sink._client is None

    async def test_missing_token_is_a_no_op(self) -> None:
        sink = _sink(_catalog(conn=_connection(), credentials={}))
        with patch(_PATCH_TARGET) as build:
            await sink.send(_notification())
        build.assert_not_called()

    async def test_close_acloses_built_client(self) -> None:
        fake = _FakeChatClient()
        sink = _sink(_catalog(conn=_connection()))
        with patch(_PATCH_TARGET, return_value=fake):
            await sink.send(_notification())
        await sink.close()
        assert fake.aclosed is True
        assert sink._client is None

    async def test_close_before_send_is_no_op(self) -> None:
        sink = _sink(_catalog(conn=_connection()))
        await sink.close()
        assert sink._client is None

    async def test_client_build_failure_degrades_to_no_op(self) -> None:
        sink = _sink(_catalog(conn=_connection()))
        with patch(_PATCH_TARGET, side_effect=ChatApiError("egress not pinned")):
            await sink.send(_notification())  # must not raise
        assert sink._client is None

    async def test_credential_resolution_failure_is_no_op(self) -> None:
        sink = _sink(
            _catalog(
                conn=_connection(),
                credentials_error=SecretRetrievalError("backend down"),
            )
        )
        with patch(_PATCH_TARGET) as build:
            await sink.send(_notification())
        build.assert_not_called()
        assert sink._client is None

    async def test_send_reraises_on_client_error(self) -> None:
        fake = _FakeChatClient()
        fake.send_message = AsyncMock(  # type: ignore[method-assign]
            side_effect=ChatApiError("post failed")
        )
        sink = _sink(_catalog(conn=_connection()))
        with (
            patch(_PATCH_TARGET, return_value=fake),
            pytest.raises(ChatApiError, match="post failed"),
        ):
            await sink.send(_notification())

    async def test_close_reraises_and_clears_on_aclose_error(self) -> None:
        fake = _FakeChatClient()
        fake.aclose = AsyncMock(  # type: ignore[method-assign]
            side_effect=ChatApiError("close failed")
        )
        sink = _sink(_catalog(conn=_connection()))
        with patch(_PATCH_TARGET, return_value=fake):
            await sink.send(_notification())
        with pytest.raises(ChatApiError, match="close failed"):
            await sink.close()
        # The broken client is not left cached for the next send.
        assert sink._client is None

    async def test_aenter_aexit(self) -> None:
        fake = _FakeChatClient()
        sink = _sink(_catalog(conn=_connection()))
        with patch(_PATCH_TARGET, return_value=fake):
            async with sink:
                await sink.send(_notification())
        assert fake.aclosed is True


class TestFormatMessage:
    def test_escapes_mrkdwn(self) -> None:
        notification = Notification(
            category=NotificationCategory.SYSTEM,
            severity=NotificationSeverity.INFO,
            title="<b>x</b> & y",
            source="s",
        )
        text = _format_message(notification)
        assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in text

    def test_omits_empty_body(self) -> None:
        notification = Notification(
            category=NotificationCategory.SYSTEM,
            severity=NotificationSeverity.INFO,
            title="t",
            source="s",
        )
        text = _format_message(notification)
        lines = text.splitlines()
        assert len(lines) == 2  # header + context line only
        assert lines[0].startswith("*[INFO]*")


class TestThreadRegistryPopulation:
    """An approval notification correlates its thread for inbound resume."""

    async def test_approval_notification_registers_thread(self) -> None:
        from synthorg.integrations.chat_api.inbound import InboundThreadRegistry

        registry = InboundThreadRegistry()
        fake = _FakeChatClient()
        sink = SlackNotificationSink(
            connection_catalog=_catalog(conn=_connection()),
            connection_name="slack-conn",
            channel="C1",
            thread_registry=registry,
        )
        notification = Notification(
            category=NotificationCategory.APPROVAL,
            severity=NotificationSeverity.WARNING,
            title="Approval required: ap-1",
            source="approval_gate",
            metadata={"approval_id": "ap-1"},
        )
        with patch(_PATCH_TARGET, return_value=fake):
            await sink.send(notification)
        assert registry.resolve(channel="C1", thread_ts="1.0") == "ap-1"

    async def test_non_approval_notification_does_not_register(self) -> None:
        from synthorg.integrations.chat_api.inbound import InboundThreadRegistry

        registry = InboundThreadRegistry()
        fake = _FakeChatClient()
        sink = SlackNotificationSink(
            connection_catalog=_catalog(conn=_connection()),
            connection_name="slack-conn",
            channel="C1",
            thread_registry=registry,
        )
        with patch(_PATCH_TARGET, return_value=fake):
            await sink.send(_notification())
        assert registry.resolve(channel="C1", thread_ts="1.0") is None

    async def test_approval_without_registry_is_a_no_op(self) -> None:
        # No thread registry wired: an APPROVAL notification must still send
        # cleanly (correlation is best-effort), not crash.
        fake = _FakeChatClient()
        sink = SlackNotificationSink(
            connection_catalog=_catalog(conn=_connection()),
            connection_name="slack-conn",
            channel="C1",
        )
        notification = Notification(
            category=NotificationCategory.APPROVAL,
            severity=NotificationSeverity.WARNING,
            title="Approval required: ap-1",
            source="approval_gate",
            metadata={"approval_id": "ap-1"},
        )
        with patch(_PATCH_TARGET, return_value=fake):
            await sink.send(notification)

    async def test_approval_without_approval_id_does_not_register(self) -> None:
        from synthorg.integrations.chat_api.inbound import InboundThreadRegistry

        registry = InboundThreadRegistry()
        fake = _FakeChatClient()
        sink = SlackNotificationSink(
            connection_catalog=_catalog(conn=_connection()),
            connection_name="slack-conn",
            channel="C1",
            thread_registry=registry,
        )
        notification = Notification(
            category=NotificationCategory.APPROVAL,
            severity=NotificationSeverity.WARNING,
            title="Approval required",
            source="approval_gate",
            metadata={},
        )
        with patch(_PATCH_TARGET, return_value=fake):
            await sink.send(notification)
        assert registry.resolve(channel="C1", thread_ts="1.0") is None
