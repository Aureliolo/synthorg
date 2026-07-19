"""Unit tests for the Slack chat client + chat-API factory.

Exercises the two-way surface (send / read / list channels / look up
user), the Slack ``ok=false`` envelope mapping (auth vs generic), HTTP
429 rate limiting, and the factory host allowlist, against
``respx``-mocked HTTP (no live Slack).
"""

import httpx
import pytest
import respx

from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.chat_api import (
    build_chat_api_client,
    chat_api_supported,
)
from synthorg.integrations.chat_api.slack import SlackChatClient
from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.errors import (
    ChatApiAuthError,
    ChatApiError,
    ChatApiRateLimitError,
)

pytestmark = pytest.mark.unit

_API = "https://slack.com/api"
_CHANNEL = NotBlankStr("C123")


def _client() -> SlackChatClient:
    return SlackChatClient(api_base_url=_API, token="xoxb-secret", timeout=5.0)


class TestSlackSendMessage:
    @respx.mock
    async def test_send_message(self) -> None:
        route = respx.post(f"{_API}/chat.postMessage").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "channel": "C123", "ts": "1.2"}
            ),
        )
        async with _client() as client:
            ref = await client.send_message(channel=_CHANNEL, text=NotBlankStr("hello"))
        assert ref.channel == "C123"
        assert ref.ts == "1.2"
        assert b'"text":"hello"' in route.calls.last.request.content
        assert b'"thread_ts"' not in route.calls.last.request.content

    @respx.mock
    async def test_send_message_in_thread(self) -> None:
        route = respx.post(f"{_API}/chat.postMessage").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "channel": "C123", "ts": "9"}
            ),
        )
        async with _client() as client:
            await client.send_message(
                channel=_CHANNEL, text=NotBlankStr("reply"), thread_ts="1.0"
            )
        assert b'"thread_ts":"1.0"' in route.calls.last.request.content


class TestSlackReads:
    @respx.mock
    async def test_read_channel(self) -> None:
        respx.get(f"{_API}/conversations.history").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True,
                    "messages": [
                        {"ts": "2", "user": "U1", "text": "hi"},
                        {"ts": "1", "user": "U2", "text": "yo", "thread_ts": "1"},
                    ],
                },
            ),
        )
        async with _client() as client:
            msgs = await client.read_channel(channel=_CHANNEL, limit=10)
        assert [m.text for m in msgs] == ["hi", "yo"]
        assert msgs[1].thread_ts == "1"

    @respx.mock
    async def test_read_thread(self) -> None:
        route = respx.get(f"{_API}/conversations.replies").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True,
                    "messages": [{"ts": "1", "user": "U1", "text": "root"}],
                },
            ),
        )
        async with _client() as client:
            msgs = await client.read_thread(
                channel=_CHANNEL, thread_ts=NotBlankStr("1"), limit=10
            )
        assert msgs[0].author == "U1"
        assert "ts=1" in str(route.calls.last.request.url)

    @respx.mock
    async def test_list_channels(self) -> None:
        respx.get(f"{_API}/conversations.list").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True,
                    "channels": [
                        {"id": "C1", "name": "general", "is_member": True},
                        {"id": "C2", "name": "secret", "is_private": True},
                    ],
                },
            ),
        )
        async with _client() as client:
            channels = await client.list_channels(limit=100)
        assert [str(c.id) for c in channels] == ["C1", "C2"]
        assert channels[1].is_private is True


class TestSlackLookupUser:
    @respx.mock
    async def test_lookup_by_id(self) -> None:
        route = respx.get(f"{_API}/users.info").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True,
                    "user": {
                        "id": "U1",
                        "name": "alice",
                        "real_name": "Alice",
                        "profile": {"email": "alice@example.com"},
                    },
                },
            ),
        )
        async with _client() as client:
            user = await client.lookup_user(user_id="U1")
        assert user.email == "alice@example.com"
        assert "user=U1" in str(route.calls.last.request.url)

    @respx.mock
    async def test_lookup_by_email(self) -> None:
        respx.get(f"{_API}/users.lookupByEmail").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "user": {"id": "U9", "name": "bob"}}
            ),
        )
        async with _client() as client:
            user = await client.lookup_user(email="bob@example.com")
        assert str(user.id) == "U9"

    @pytest.mark.parametrize(
        "kwargs",
        [
            {},  # neither selector
            {"user_id": "U1", "email": "a@example.com"},  # both selectors
        ],
    )
    async def test_lookup_bad_selector_fails(self, kwargs: dict[str, str]) -> None:
        # The "exactly one of id/email" precondition is enforced by the
        # client itself, not only the tool arg validator, so any other
        # caller of the Protocol is protected too.
        async with _client() as client:
            with pytest.raises(ChatApiError):
                await client.lookup_user(**kwargs)


class TestSlackEnvelopeMapping:
    @respx.mock
    @pytest.mark.parametrize("error", ["invalid_auth", "missing_scope", "not_authed"])
    async def test_auth_errors_map_to_auth_error(self, error: str) -> None:
        respx.get(f"{_API}/conversations.list").mock(
            return_value=httpx.Response(200, json={"ok": False, "error": error}),
        )
        async with _client() as client:
            with pytest.raises(ChatApiAuthError):
                await client.list_channels(limit=10)

    @respx.mock
    async def test_generic_error_maps_to_chat_error(self) -> None:
        respx.get(f"{_API}/conversations.list").mock(
            return_value=httpx.Response(
                200, json={"ok": False, "error": "channel_not_found"}
            ),
        )
        async with _client() as client:
            with pytest.raises(ChatApiError):
                await client.list_channels(limit=10)

    @respx.mock
    async def test_http_429_maps_to_rate_limit(self) -> None:
        respx.post(f"{_API}/chat.postMessage").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "7"}),
        )
        async with _client() as client:
            with pytest.raises(ChatApiRateLimitError) as exc_info:
                await client.send_message(channel=_CHANNEL, text=NotBlankStr("x"))
        assert exc_info.value.retry_after_seconds == 7.0


class TestChatFactory:
    def test_builds_slack(self) -> None:
        client = build_chat_api_client(
            connection_type=ConnectionType.SLACK, base_url="", token="t", timeout=5.0
        )
        assert isinstance(client, SlackChatClient)

    def test_default_base_url(self) -> None:
        client = build_chat_api_client(
            connection_type=ConnectionType.SLACK, base_url="", token="t", timeout=5.0
        )
        assert isinstance(client, SlackChatClient)
        assert client._api_base_url == "https://slack.com/api/"

    def test_rejects_non_slack_host(self) -> None:
        with pytest.raises(ChatApiError):
            build_chat_api_client(
                connection_type=ConnectionType.SLACK,
                base_url="https://evil.example.com",
                token="t",
                timeout=5.0,
            )

    def test_supported_predicate(self) -> None:
        assert chat_api_supported(ConnectionType.SLACK) is True
        assert chat_api_supported(ConnectionType.GITHUB) is False

    def test_unsupported_type_raises(self) -> None:
        with pytest.raises(StrategyFactoryNotFoundError):
            build_chat_api_client(
                connection_type=ConnectionType.GITHUB,
                base_url="",
                token="t",
                timeout=5.0,
            )
