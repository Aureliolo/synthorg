"""Tests for the session-less dashboard SSE stream (``_dashboard``)."""

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from litestar import Request
from litestar.channels import ChannelsPlugin, Subscriber
from litestar.datastructures import State
from litestar.exceptions import NotAuthorizedException, ServiceUnavailableException

from synthorg.api.channels import BUDGET_CHANNELS, user_channel
from synthorg.api.controllers.events._dashboard import (
    _DASHBOARD_REPLAY_LIMIT,
    _dashboard_frame,
    _stream_dashboard_frames,
    dashboard_channel_frames,
    resolve_dashboard_channels,
)
from synthorg.api.controllers.events.stream import _require_dashboard_feed
from synthorg.core.auth.models import AuthenticatedUser, AuthMethod
from synthorg.core.auth.roles import HumanRole
from synthorg.core.clock import SystemClock
from tests._shared import mock_of


def _ws_event_json() -> str:
    return json.dumps(
        {
            "event_type": "task.updated",
            "channel": "tasks",
            "timestamp": "t",
            "payload": {},
        }
    )


def _auth_user(
    *,
    user_id: str = "u-001",
    role: HumanRole = HumanRole.OBSERVER,
) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        username="alice",
        role=role,
        auth_method=AuthMethod.JWT,
        session_id=None,
        api_key_id=None,
    )


async def _aiter(events: list[bytes]) -> AsyncIterator[bytes]:
    for event in events:
        yield event


def _plugin_with(events: list[bytes]) -> ChannelsPlugin:
    """A spec'd ``ChannelsPlugin`` mock whose subscriber replays ``events``."""
    subscriber = mock_of[Subscriber](iter_events=lambda: _aiter(events))
    plugin: ChannelsPlugin = mock_of[ChannelsPlugin](
        subscribe=AsyncMock(return_value=subscriber),
        unsubscribe=AsyncMock(),
    )
    return plugin


@pytest.mark.unit
class TestResolveDashboardChannels:
    def test_observer_excludes_budget_channels_and_includes_user_channel(self) -> None:
        user = _auth_user(role=HumanRole.OBSERVER)
        channels = resolve_dashboard_channels(user)
        assert user_channel("u-001") in channels
        assert "tasks" in channels
        assert not (BUDGET_CHANNELS & set(channels))

    def test_ceo_receives_budget_channels(self) -> None:
        user = _auth_user(role=HumanRole.CEO)
        channels = resolve_dashboard_channels(user)
        assert set(channels) >= BUDGET_CHANNELS


def _request_with(
    *, user: object, plugins: list[object]
) -> Request[object, object, State]:
    """A spec'd ``Request`` whose ``app.plugins`` and ``user`` are stubbed."""
    app = SimpleNamespace(plugins=plugins)
    return cast(
        "Request[object, object, State]",
        mock_of[Request](user=user, app=app),
    )


@pytest.mark.unit
class TestRequireDashboardFeed:
    def test_rejects_unauthenticated_caller(self) -> None:
        # Fail closed: no authenticated user -> 401, never a live stream.
        with pytest.raises(NotAuthorizedException):
            _require_dashboard_feed(_request_with(user=None, plugins=[]))

    def test_503_when_channel_plugin_unwired(self) -> None:
        with pytest.raises(ServiceUnavailableException):
            _require_dashboard_feed(_request_with(user=_auth_user(), plugins=[]))

    def test_returns_plugin_and_user_when_wired(self) -> None:
        user = _auth_user()
        plugin = mock_of[ChannelsPlugin]()
        got_plugin, got_user = _require_dashboard_feed(
            _request_with(user=user, plugins=[plugin])
        )
        assert got_plugin is plugin
        assert got_user is user


@pytest.mark.unit
class TestDashboardFrame:
    def test_valid_event_renders_named_ws_frame_with_id(self) -> None:
        payload = _ws_event_json()
        frame = _dashboard_frame(payload, 3)
        assert frame == {"event": "ws", "data": payload, "id": "3"}

    @pytest.mark.parametrize(
        "data",
        ["{not-json", json.dumps([1, 2]), json.dumps({"channel": "tasks"})],
        ids=["bad_json", "array", "missing_event_type"],
    )
    def test_malformed_payload_is_dropped(self, data: str) -> None:
        assert _dashboard_frame(data, 0) is None


@pytest.mark.unit
class TestDashboardChannelFrames:
    async def test_forwards_published_event_as_ws_frame(self) -> None:
        event = _ws_event_json().encode("utf-8")
        plugin = _plugin_with([event])
        frames = [
            frame
            async for frame in dashboard_channel_frames(
                plugin,
                ["tasks"],
                app_state=None,
                replay=False,
            )
        ]
        assert frames == [{"event": "ws", "data": event.decode("utf-8"), "id": "0"}]
        plugin.subscribe.assert_awaited_once_with(["tasks"], history=None)  # type: ignore[attr-defined]
        plugin.unsubscribe.assert_awaited_once_with(  # type: ignore[attr-defined]
            plugin.subscribe.return_value  # type: ignore[attr-defined]
        )

    async def test_replay_subscribes_with_backlog_and_forwards_events(self) -> None:
        event = _ws_event_json().encode("utf-8")
        plugin = _plugin_with([event])
        frames = [
            frame
            async for frame in dashboard_channel_frames(
                plugin,
                ["tasks"],
                app_state=None,
                replay=True,
            )
        ]
        # Replay subscribes with the backlog history AND forwards the replayed
        # events as ``ws`` frames (not just the subscribe argument).
        assert frames == [{"event": "ws", "data": event.decode("utf-8"), "id": "0"}]
        plugin.subscribe.assert_awaited_once_with(  # type: ignore[attr-defined]
            ["tasks"], history=_DASHBOARD_REPLAY_LIMIT
        )
        plugin.unsubscribe.assert_awaited_once_with(  # type: ignore[attr-defined]
            plugin.subscribe.return_value  # type: ignore[attr-defined]
        )

    async def test_unsubscribes_when_consumer_stops_mid_stream(self) -> None:
        # A client disconnect closes the generator before the backlog is
        # drained; the ``finally`` must still tear the subscription down once.
        event = _ws_event_json().encode("utf-8")
        plugin = _plugin_with([event, event])
        gen = cast(
            AsyncGenerator[dict[str, str]],
            dashboard_channel_frames(plugin, ["tasks"], app_state=None, replay=False),
        )
        first = await anext(gen)
        assert first["event"] == "ws"
        await gen.aclose()
        plugin.unsubscribe.assert_awaited_once_with(  # type: ignore[attr-defined]
            plugin.subscribe.return_value  # type: ignore[attr-defined]
        )

    async def test_emits_keepalive_frame_when_idle(self) -> None:
        # A subscriber that never yields drives the keepalive timeout branch:
        # a zero keepalive interval makes the wait_for time out immediately.
        async def _never() -> AsyncIterator[bytes]:
            await asyncio.Event().wait()
            yield b""  # pragma: no cover -- unreachable; the wait never returns

        subscriber = mock_of[Subscriber](iter_events=_never)
        frames: list[dict[str, str]] = []
        async for frame in _stream_dashboard_frames(
            subscriber, keepalive_seconds=0.0, clock=SystemClock()
        ):
            frames.append(frame)
            break
        assert frames == [{"event": "keepalive", "data": "{}"}]
