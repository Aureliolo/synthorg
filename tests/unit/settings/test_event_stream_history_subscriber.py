"""Tests for ``EventStreamHistorySettingsSubscriber``.

A change to ``communication.event_stream_history_max_sessions`` /
``event_stream_history_per_session`` pushes the new bound onto the live
``EventStreamHub`` ledger. Tests assert each key routes to the matching hub
setter with the resolved value, the ``hub is None`` path is a silent no-op,
resolver failure re-raises without mutating the ledger, and an unexpected pair
no-ops.
"""

from unittest.mock import create_autospec

import pytest

from synthorg.api.state import AppState
from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.config.schema import RootConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.event_stream_history_subscriber import (
    EventStreamHistorySettingsSubscriber,
)
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _make_subscriber(
    *,
    hub: EventStreamHub | None,
    int_return: int = 64,
    int_side_effect: BaseException | None = None,
) -> EventStreamHistorySettingsSubscriber:
    resolver = create_autospec(ConfigResolver, instance=True)
    if int_side_effect is not None:
        resolver.get_int.side_effect = int_side_effect
    else:
        resolver.get_int.return_value = int_return
    app_state: AppState = make_app_state(
        config=RootConfig(company_name="test"),
        config_resolver=resolver,
        event_stream_hub=hub,
    )
    return EventStreamHistorySettingsSubscriber(
        app_state=app_state,
        settings_service=create_autospec(SettingsService, instance=True),
    )


class TestProtocol:
    def test_isinstance(self) -> None:
        sub = _make_subscriber(hub=mock_of[EventStreamHub]())
        assert isinstance(sub, SettingsSubscriber)

    def test_watched_keys(self) -> None:
        sub = _make_subscriber(hub=mock_of[EventStreamHub]())
        assert sub.watched_keys == frozenset(
            {
                ("communication", "event_stream_history_max_sessions"),
                ("communication", "event_stream_history_per_session"),
            }
        )


class TestApply:
    async def test_max_sessions_routes_to_hub(self) -> None:
        hub = mock_of[EventStreamHub]()
        sub = _make_subscriber(hub=hub, int_return=16)
        await sub.on_settings_changed(
            "communication", "event_stream_history_max_sessions"
        )
        hub.set_history_max_sessions.assert_called_once_with(16)
        hub.set_history_per_session.assert_not_called()

    async def test_per_session_routes_to_hub(self) -> None:
        hub = mock_of[EventStreamHub]()
        sub = _make_subscriber(hub=hub, int_return=8)
        await sub.on_settings_changed(
            "communication", "event_stream_history_per_session"
        )
        hub.set_history_per_session.assert_called_once_with(8)
        hub.set_history_max_sessions.assert_not_called()

    async def test_no_hub_is_silent_noop(self) -> None:
        sub = _make_subscriber(hub=None)
        # No hub wired; resolving + applying must not raise.
        await sub.on_settings_changed(
            "communication", "event_stream_history_max_sessions"
        )

    async def test_resolver_failure_reraises(self) -> None:
        hub = mock_of[EventStreamHub]()
        sub = _make_subscriber(hub=hub, int_side_effect=RuntimeError("resolver outage"))
        with pytest.raises(RuntimeError, match="resolver outage"):
            await sub.on_settings_changed(
                "communication", "event_stream_history_max_sessions"
            )
        hub.set_history_max_sessions.assert_not_called()

    async def test_unknown_key_is_noop(self) -> None:
        hub = mock_of[EventStreamHub]()
        sub = _make_subscriber(hub=hub)
        await sub.on_settings_changed("communication", "unrelated")
        hub.set_history_max_sessions.assert_not_called()
