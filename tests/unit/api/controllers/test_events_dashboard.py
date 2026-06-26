"""Tests for the session-less dashboard SSE stream (``_dashboard``)."""

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from litestar.channels import ChannelsPlugin

from synthorg.api.channels import BUDGET_CHANNELS, user_channel
from synthorg.api.controllers.events._dashboard import (
    _DASHBOARD_REPLAY_LIMIT,
    _dashboard_frame,
    dashboard_channel_frames,
    resolve_dashboard_channels,
)
from synthorg.core.auth.models import AuthenticatedUser, AuthMethod
from synthorg.core.auth.roles import HumanRole
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
    subscriber = SimpleNamespace(iter_events=lambda: _aiter(events))
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
        plugin.unsubscribe.assert_awaited_once()  # type: ignore[attr-defined]

    async def test_replay_subscribes_with_backlog_history(self) -> None:
        plugin = _plugin_with([])
        _ = [
            frame
            async for frame in dashboard_channel_frames(
                plugin,
                ["tasks"],
                app_state=None,
                replay=True,
            )
        ]
        plugin.subscribe.assert_awaited_once_with(  # type: ignore[attr-defined]
            ["tasks"], history=_DASHBOARD_REPLAY_LIMIT
        )
        plugin.unsubscribe.assert_awaited_once()  # type: ignore[attr-defined]
