"""Tests for ``NotificationsBridgeSettingsSubscriber``.

A change to a watched ``notifications.*`` key rebuilds the notification
dispatcher (and its sinks) from the DB-resolved bridge config via
``_apply_notification_dispatcher_config``. Tests assert the rebuild fires on a
watched key, no-ops on an unexpected pair, and re-raises a rebuild failure.
"""

from unittest.mock import create_autospec

import pytest

from synthorg.api.lifecycle_helpers import config_apply
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.notifications_bridge_subscriber import (
    NotificationsBridgeSettingsSubscriber,
)
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _make_subscriber() -> tuple[
    NotificationsBridgeSettingsSubscriber, AppState, RootConfig
]:
    config = RootConfig(company_name="test")
    app_state = make_app_state(config=config)
    sub = NotificationsBridgeSettingsSubscriber(
        app_state=app_state,
        config=config,
        settings_service=create_autospec(SettingsService, instance=True),
    )
    return sub, app_state, config


class TestProtocol:
    def test_isinstance(self) -> None:
        sub, _, _ = _make_subscriber()
        assert isinstance(sub, SettingsSubscriber)

    def test_watched_keys_cover_slack_ntfy_email(self) -> None:
        sub, _, _ = _make_subscriber()
        watched = sub.watched_keys
        assert ("notifications", "slack_webhook_timeout_seconds") in watched
        assert ("notifications", "ntfy_default_url") in watched
        assert ("notifications", "email_smtp_timeout_seconds") in watched


class TestRebuild:
    async def test_watched_change_rebuilds_dispatcher(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = create_autospec(config_apply._apply_notification_dispatcher_config)
        monkeypatch.setattr(config_apply, "_apply_notification_dispatcher_config", spy)
        sub, app_state, config = _make_subscriber()
        await sub.on_settings_changed("notifications", "slack_default_webhook_url")
        spy.assert_awaited_once_with(app_state, config)

    async def test_unknown_key_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = create_autospec(config_apply._apply_notification_dispatcher_config)
        monkeypatch.setattr(config_apply, "_apply_notification_dispatcher_config", spy)
        sub, _, _ = _make_subscriber()
        await sub.on_settings_changed("notifications", "unrelated")
        spy.assert_not_awaited()

    async def test_rebuild_failure_reraises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = create_autospec(config_apply._apply_notification_dispatcher_config)
        spy.side_effect = RuntimeError("dispatcher boom")
        monkeypatch.setattr(config_apply, "_apply_notification_dispatcher_config", spy)
        sub, _, _ = _make_subscriber()
        with pytest.raises(RuntimeError, match="dispatcher boom"):
            await sub.on_settings_changed("notifications", "ntfy_default_url")
