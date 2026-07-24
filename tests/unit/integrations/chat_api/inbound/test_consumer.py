"""Tests for the inbound Socket-Mode consumer loop + kill-switch."""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from synthorg.integrations.chat_api.inbound import consumer as consumer_mod
from synthorg.integrations.chat_api.inbound.consumer import ChatInboundConsumer
from synthorg.integrations.chat_api.inbound.models import (
    InboundChatEvent,
    InboundEventKind,
)
from synthorg.integrations.chat_api.inbound.registry import InboundThreadRegistry
from synthorg.integrations.chat_api.inbound.router import InboundResumeRouter
from synthorg.integrations.chat_api.inbound.socket_mode import WsConnector
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.settings.resolver import ConfigResolver
from tests._shared.fake_clock import FakeClock
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.unit

_API = "https://slack.com/api"


def _resolver(
    *, enabled: bool = True, connection: str = "slack-conn", raise_on_bool: bool = False
) -> ConfigResolver:
    get_bool = AsyncMock(spec=ConfigResolver.get_bool)
    if raise_on_bool:
        get_bool.side_effect = RuntimeError("settings down")
    else:
        get_bool.return_value = enabled
    resolver: ConfigResolver = mock_of[ConfigResolver](
        get_bool=get_bool,
        get_str=AsyncMock(spec=ConfigResolver.get_str, return_value=connection),
    )
    return resolver


def _catalog(credentials: dict[str, str]) -> ConnectionCatalog:
    catalog: ConnectionCatalog = mock_of[ConnectionCatalog](
        get_credentials=AsyncMock(
            spec=ConnectionCatalog.get_credentials, return_value=credentials
        ),
    )
    return catalog


class _FakeWsSession:
    def __init__(self, frames: list[dict[str, object]]) -> None:
        self._frames = frames

    def __aiter__(self) -> AsyncIterator[Mapping[str, object]]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[Mapping[str, object]]:
        for frame in self._frames:
            yield frame

    async def ack(self, _envelope_id: str) -> None:
        return None


def _connector(session: _FakeWsSession) -> WsConnector:
    @asynccontextmanager
    async def _connect(_url: str) -> AsyncIterator[_FakeWsSession]:
        yield session

    return _connect


class _RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def resume(self, *, approval_id: str, **_kwargs: object) -> bool:
        self.calls.append(approval_id)
        return True


def _router(dispatcher: _RecordingDispatcher) -> InboundResumeRouter:
    registry = InboundThreadRegistry()
    registry.register(channel="C1", thread_ts="1.0", approval_id="ap-1")
    return InboundResumeRouter(registry=registry, dispatcher=dispatcher)


def _consumer(
    *,
    catalog: ConnectionCatalog,
    session: _FakeWsSession,
    dispatcher: _RecordingDispatcher,
) -> ChatInboundConsumer:
    return ChatInboundConsumer(
        connection_catalog=catalog,
        router=_router(dispatcher),
        connector=_connector(session),
        config_resolver=_resolver(),
        clock=FakeClock(),
    )


class TestKillSwitch:
    async def test_resolve_enabled_false_without_resolver(self) -> None:
        consumer = ChatInboundConsumer(
            connection_catalog=_catalog({}),
            router=_router(_RecordingDispatcher()),
            clock=FakeClock(),
        )
        assert await consumer._resolve_enabled() is False

    async def test_resolve_enabled_reads_setting(self) -> None:
        consumer = ChatInboundConsumer(
            connection_catalog=_catalog({}),
            router=_router(_RecordingDispatcher()),
            config_resolver=_resolver(enabled=True),
            clock=FakeClock(),
        )
        assert await consumer._resolve_enabled() is True

    async def test_resolve_enabled_fails_closed_on_error(self) -> None:
        consumer = ChatInboundConsumer(
            connection_catalog=_catalog({}),
            router=_router(_RecordingDispatcher()),
            config_resolver=_resolver(raise_on_bool=True),
            clock=FakeClock(),
        )
        assert await consumer._resolve_enabled() is False


class TestSession:
    async def test_no_app_token_skips_connect(self) -> None:
        dispatcher = _RecordingDispatcher()
        session = _FakeWsSession([{"type": "disconnect"}])
        consumer = _consumer(
            catalog=_catalog({"token": "xoxb-only"}),
            session=session,
            dispatcher=dispatcher,
        )
        await consumer._connect_and_stream("slack-conn")
        assert dispatcher.calls == []

    @respx.mock
    async def test_streams_events_when_token_present(self) -> None:
        respx.post(f"{_API}/apps.connections.open").mock(
            return_value=httpx.Response(200, json={"ok": True, "url": "wss://gw/x"}),
        )
        dispatcher = _RecordingDispatcher()
        session = _FakeWsSession(
            [
                {
                    "type": "events_api",
                    "envelope_id": "e1",
                    "payload": {
                        "event": {
                            "type": "reaction_added",
                            "user": "U1",
                            "reaction": "white_check_mark",
                            "item": {"channel": "C1", "ts": "1.0"},
                        }
                    },
                },
                {"type": "disconnect"},
            ]
        )
        consumer = _consumer(
            catalog=_catalog({"app_token": "xapp-x"}),
            session=session,
            dispatcher=dispatcher,
        )
        await consumer._connect_and_stream("slack-conn")
        # The approve reaction on the item (ts 1.0 in C1) resolves to the
        # registered approval and drives a resume (an explicit signal;
        # a text reply would not).
        assert dispatcher.calls == ["ap-1"]


class TestResolveConnectionName:
    async def test_none_resolver_returns_empty(self) -> None:
        consumer = ChatInboundConsumer(
            connection_catalog=_catalog({}),
            router=_router(_RecordingDispatcher()),
            clock=FakeClock(),
        )
        assert await consumer._resolve_connection_name() == ""

    async def test_resolver_error_fails_closed(self) -> None:
        resolver: ConfigResolver = mock_of[ConfigResolver](
            get_bool=AsyncMock(spec=ConfigResolver.get_bool, return_value=True),
            get_str=AsyncMock(
                spec=ConfigResolver.get_str, side_effect=RuntimeError("down")
            ),
        )
        consumer = ChatInboundConsumer(
            connection_catalog=_catalog({}),
            router=_router(_RecordingDispatcher()),
            config_resolver=resolver,
            clock=FakeClock(),
        )
        assert await consumer._resolve_connection_name() == ""


class TestSafeRoute:
    async def test_bad_event_does_not_propagate(self) -> None:
        # A router that raises must not crash the socket loop; _safe_route
        # isolates it.
        router: InboundResumeRouter = mock_of[InboundResumeRouter](
            route=AsyncMock(
                spec=InboundResumeRouter.route, side_effect=RuntimeError("boom")
            ),
        )
        consumer = ChatInboundConsumer(
            connection_catalog=_catalog({}),
            router=router,
            config_resolver=_resolver(),
            clock=FakeClock(),
        )
        event = InboundChatEvent(
            kind=InboundEventKind.MENTION, channel="C1", user="U1", text="hi"
        )
        # Does not raise.
        await consumer._safe_route(event)


class TestLifecycle:
    async def test_start_then_stop_is_clean(self) -> None:
        consumer = ChatInboundConsumer(
            connection_catalog=_catalog({}),
            router=_router(_RecordingDispatcher()),
            config_resolver=_resolver(enabled=False),
            clock=FakeClock(),
        )
        await consumer.start()
        await asyncio.sleep(0)
        await consumer.stop()
        # Idempotent second stop.
        await consumer.stop()

    async def test_start_is_idempotent_while_running(self) -> None:
        consumer = ChatInboundConsumer(
            connection_catalog=_catalog({}),
            router=_router(_RecordingDispatcher()),
            config_resolver=_resolver(enabled=False),
            clock=FakeClock(),
        )
        await consumer.start()
        await asyncio.sleep(0)
        first = consumer._task
        await consumer.start()
        # A second start while the loop runs must not spawn a duplicate task.
        assert consumer._task is first
        await consumer.stop()

    async def test_stop_times_out_on_uncooperative_task(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(consumer_mod, "_STOP_TIMEOUT_SECONDS", 0.05)
        consumer = ChatInboundConsumer(
            connection_catalog=_catalog({}),
            router=_router(_RecordingDispatcher()),
            config_resolver=_resolver(enabled=False),
            clock=FakeClock(),
        )

        async def _stubborn() -> None:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                # Swallow the first cancel to force stop()'s timeout branch;
                # the second cancel from stop() ends it.
                await asyncio.sleep(3600)

        task = asyncio.create_task(_stubborn())
        await asyncio.sleep(0)
        consumer._task = task
        # Returns after the (patched) timeout rather than hanging on the
        # inner 10s budget; the task is left cancelled, not awaited forever.
        await consumer.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
