"""Tests for SecuritySubscriber."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.settings.service import SettingsService
from synthorg.settings.subscribers.security_subscriber import (
    SecuritySubscriber,
)

pytestmark = pytest.mark.unit


def _make_subscriber(
    *,
    raw_value: str = '["host.docker.internal:11434"]',
) -> tuple[SecuritySubscriber, AsyncMock, AsyncMock]:
    """Create a subscriber with mocked dependencies."""
    settings_service = AsyncMock(spec=SettingsService)
    setting_result = MagicMock()
    setting_result.value = raw_value
    settings_service.get.return_value = setting_result

    callback = AsyncMock()
    sub = SecuritySubscriber(
        settings_service=settings_service,
        on_allowlist_changed=callback,
    )
    return sub, callback, settings_service


class TestSecuritySubscriber:
    """Tests for SecuritySubscriber."""

    def test_watched_keys(self) -> None:
        sub, _, _ = _make_subscriber()
        assert ("providers", "discovery_allowlist") in sub.watched_keys

    def test_subscriber_name(self) -> None:
        sub, _, _ = _make_subscriber()
        assert sub.subscriber_name == "security-discovery-allowlist"

    async def test_ignores_unrelated_keys(self) -> None:
        sub, callback, _ = _make_subscriber()
        await sub.on_settings_changed("security", "enabled")
        callback.assert_not_called()

    async def test_invokes_callback_on_allowlist_change(self) -> None:
        sub, callback, _ = _make_subscriber()
        await sub.on_settings_changed(
            "providers",
            "discovery_allowlist",
        )
        callback.assert_awaited_once_with(
            ("host.docker.internal:11434",),
        )

    async def test_handles_empty_allowlist(self) -> None:
        sub, callback, _ = _make_subscriber(raw_value="[]")
        await sub.on_settings_changed(
            "providers",
            "discovery_allowlist",
        )
        callback.assert_awaited_once_with(())

    async def test_handles_malformed_json(self) -> None:
        sub, callback, _ = _make_subscriber(raw_value="not-json")
        await sub.on_settings_changed(
            "providers",
            "discovery_allowlist",
        )
        callback.assert_not_called()

    async def test_handles_non_array_json(self) -> None:
        sub, callback, _ = _make_subscriber(raw_value='{"key": "val"}')
        await sub.on_settings_changed(
            "providers",
            "discovery_allowlist",
        )
        callback.assert_not_called()

    async def test_handles_missing_setting(self) -> None:
        sub, callback, settings_service = _make_subscriber()
        settings_service.get.return_value = None
        await sub.on_settings_changed(
            "providers",
            "discovery_allowlist",
        )
        callback.assert_awaited_once_with(())
