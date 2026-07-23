"""Tests for the Slack Socket-Mode client (open + stream, injected ws)."""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

import httpx
import pytest
import respx

from synthorg.integrations.chat_api.inbound.models import InboundChatEvent
from synthorg.integrations.chat_api.inbound.socket_mode import SlackSocketModeClient
from synthorg.integrations.errors import ChatApiAuthError, ChatApiError

pytestmark = pytest.mark.unit

_API = "https://slack.com/api"


class _FakeWsSession:
    """Yields canned frames and records acks."""

    def __init__(self, frames: list[dict[str, object]]) -> None:
        self._frames = frames
        self.acked: list[str] = []

    def __aiter__(self) -> AsyncIterator[Mapping[str, object]]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[Mapping[str, object]]:
        for frame in self._frames:
            yield frame

    async def ack(self, envelope_id: str) -> None:
        self.acked.append(envelope_id)


def _connector(session: _FakeWsSession):
    @asynccontextmanager
    async def _connect(url: str) -> AsyncIterator[_FakeWsSession]:
        assert url.startswith("wss://")
        yield session

    return _connect


def _client(session: _FakeWsSession) -> SlackSocketModeClient:
    return SlackSocketModeClient(
        app_token="xapp-secret",
        connector=_connector(session),
        api_base_url=_API,
        timeout=5.0,
    )


def _open_ok() -> respx.Route:
    return respx.post(f"{_API}/apps.connections.open").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "url": "wss://gw.slack.com/link"}
        ),
    )


class TestStream:
    @respx.mock
    async def test_acks_and_dispatches_then_stops_on_disconnect(self) -> None:
        _open_ok()
        session = _FakeWsSession(
            [
                {"type": "hello"},
                {
                    "type": "events_api",
                    "envelope_id": "env-1",
                    "payload": {
                        "event": {
                            "type": "app_mention",
                            "user": "U1",
                            "text": "hi",
                            "ts": "1.0",
                            "channel": "C1",
                        }
                    },
                },
                {"type": "disconnect"},
                # Never reached: stream returns on disconnect.
                {
                    "type": "events_api",
                    "envelope_id": "env-2",
                    "payload": {
                        "event": {"type": "app_mention", "user": "U2", "channel": "C1"}
                    },
                },
            ]
        )
        received: list[InboundChatEvent] = []

        async def _on_event(event: InboundChatEvent) -> None:
            received.append(event)

        await _client(session).stream(on_event=_on_event)

        assert session.acked == ["env-1"]
        assert len(received) == 1
        assert received[0].user == "U1"

    @respx.mock
    async def test_open_auth_error_raises(self) -> None:
        respx.post(f"{_API}/apps.connections.open").mock(
            return_value=httpx.Response(
                200, json={"ok": False, "error": "invalid_auth"}
            ),
        )
        session = _FakeWsSession([])
        with pytest.raises(ChatApiAuthError):
            await _client(session).stream(on_event=_noop)

    @respx.mock
    async def test_open_missing_url_raises(self) -> None:
        respx.post(f"{_API}/apps.connections.open").mock(
            return_value=httpx.Response(200, json={"ok": True}),
        )
        session = _FakeWsSession([])
        with pytest.raises(ChatApiError):
            await _client(session).stream(on_event=_noop)


async def _noop(_event: InboundChatEvent) -> None:
    return None
