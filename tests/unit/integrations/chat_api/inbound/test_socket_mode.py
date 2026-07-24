"""Tests for the Slack Socket-Mode client (open + stream, injected ws)."""

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager

import aiohttp
import httpx
import pytest
import respx

from synthorg.integrations.chat_api.inbound.models import InboundChatEvent
from synthorg.integrations.chat_api.inbound.socket_mode import (
    SlackSocketModeClient,
    WsConnector,
    _AiohttpWsSession,
)
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


def _connector(session: _FakeWsSession) -> WsConnector:
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

    @respx.mock
    async def test_open_transport_error_maps_to_chat_api_error(self) -> None:
        respx.post(f"{_API}/apps.connections.open").mock(
            side_effect=httpx.ConnectError("dns down")
        )
        session = _FakeWsSession([])
        with pytest.raises(ChatApiError, match="transport error"):
            await _client(session).stream(on_event=_noop)

    @respx.mock
    async def test_open_non_json_body_maps_to_chat_api_error(self) -> None:
        # A proxy or captive portal can answer 200 with HTML; the decode
        # failure must surface as a typed transport error, not a bare
        # ValueError escaping the connect path.
        respx.post(f"{_API}/apps.connections.open").mock(
            return_value=httpx.Response(200, text="<html>proxy</html>"),
        )
        session = _FakeWsSession([])
        with pytest.raises(ChatApiError):
            await _client(session).stream(on_event=_noop)

    @respx.mock
    async def test_open_generic_error_is_not_auth_error(self) -> None:
        # A non-auth ``ok=false`` code raises a plain ChatApiError, not the
        # auth subclass (which is reserved for the auth/scope codes).
        respx.post(f"{_API}/apps.connections.open").mock(
            return_value=httpx.Response(
                200, json={"ok": False, "error": "ratelimited"}
            ),
        )
        session = _FakeWsSession([])
        with pytest.raises(ChatApiError) as exc_info:
            await _client(session).stream(on_event=_noop)
        assert not isinstance(exc_info.value, ChatApiAuthError)

    @respx.mock
    async def test_ack_runs_after_dispatch(self) -> None:
        # At-least-once: the event is dispatched before its envelope is acked.
        _open_ok()
        order: list[str] = []
        session = _FakeWsSession(
            [
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
            ]
        )
        original_ack = session.ack

        async def _record_ack(envelope_id: str) -> None:
            order.append(f"ack:{envelope_id}")
            await original_ack(envelope_id)

        session.ack = _record_ack  # type: ignore[method-assign]

        async def _on_event(_event: InboundChatEvent) -> None:
            order.append("dispatch")

        await _client(session).stream(on_event=_on_event)
        assert order == ["dispatch", "ack:env-1"]


class TestConstruction:
    def test_blank_app_token_rejected(self) -> None:
        with pytest.raises(ValueError, match="app_token"):
            SlackSocketModeClient(
                app_token="",
                connector=_connector(_FakeWsSession([])),
                api_base_url=_API,
                timeout=5.0,
            )

    def test_non_positive_timeout_rejected(self) -> None:
        with pytest.raises(ValueError, match="timeout"):
            SlackSocketModeClient(
                app_token="xapp-x",
                connector=_connector(_FakeWsSession([])),
                api_base_url=_API,
                timeout=0.0,
            )


class _FakeAiohttpWs:
    """A stand-in for aiohttp's ws with a canned message stream.

    Not a subclass: the tests patch ``aiohttp.ClientWebSocketResponse`` to
    this type so the adapter's ``isinstance`` narrowing accepts it without
    dragging in the real transport's constructor or typing surface.
    """

    def __init__(self, messages: Sequence[object]) -> None:
        self._messages = messages
        self.sent: list[object] = []

    def __aiter__(self) -> AsyncIterator[object]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[object]:
        for msg in self._messages:
            yield msg

    async def send_json(self, data: object) -> None:
        self.sent.append(data)


class TestAiohttpSession:
    async def test_frames_yields_only_text_mappings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(aiohttp, "ClientWebSocketResponse", _FakeAiohttpWs)
        text = aiohttp.WSMsgType.TEXT
        messages = [
            aiohttp.WSMessage(text, json.dumps({"type": "hello"}), ""),
            aiohttp.WSMessage(aiohttp.WSMsgType.PING, b"", ""),
            aiohttp.WSMessage(text, json.dumps([1, 2]), ""),
            aiohttp.WSMessage(text, json.dumps({"type": "disconnect"}), ""),
        ]
        session = _AiohttpWsSession(_FakeAiohttpWs(messages))
        frames = [frame async for frame in session]
        # Only the two JSON-object TEXT frames survive (ping + array dropped).
        assert frames == [{"type": "hello"}, {"type": "disconnect"}]

    async def test_ack_sends_envelope_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(aiohttp, "ClientWebSocketResponse", _FakeAiohttpWs)
        ws = _FakeAiohttpWs([])
        session = _AiohttpWsSession(ws)
        await session.ack("env-9")
        assert ws.sent == [{"envelope_id": "env-9"}]


async def _noop(_event: InboundChatEvent) -> None:
    return None
