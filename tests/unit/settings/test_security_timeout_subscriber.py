"""Tests for SecurityTimeoutSettingsSubscriber.

Watches ``security.timeout_check_interval_seconds`` and reschedules
the approval-timeout scheduler on operator changes.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.settings.models import SettingValue
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.security_timeout_subscriber import (
    SecurityTimeoutSettingsSubscriber,
)


def _make_subscriber(
    *,
    interval_value: str = "12.5",
    raise_on_get: bool = False,
    reschedule_raises: bool = False,
) -> tuple[SecurityTimeoutSettingsSubscriber, MagicMock, MagicMock]:
    """Create a subscriber with mock scheduler + settings service."""
    scheduler = MagicMock(spec=ApprovalTimeoutScheduler)
    if reschedule_raises:
        scheduler.reschedule.side_effect = ValueError(
            "interval_seconds must be positive",
        )

    settings_service = MagicMock(spec=SettingsService)

    async def _mock_get(namespace: str, key: str) -> MagicMock:
        del namespace, key
        if raise_on_get:
            msg = "settings backend down"
            raise RuntimeError(msg)
        result = MagicMock(spec=SettingValue)
        result.value = interval_value
        return result

    settings_service.get = AsyncMock(
        spec=SettingsService.get,
        side_effect=_mock_get,
    )

    sub = SecurityTimeoutSettingsSubscriber(
        scheduler=scheduler,
        settings_service=settings_service,
    )
    return sub, scheduler, settings_service


@pytest.mark.unit
class TestSubscriberProtocol:
    """SecurityTimeoutSettingsSubscriber conforms to SettingsSubscriber."""

    def test_isinstance_check(self) -> None:
        sub, _, _ = _make_subscriber()
        assert isinstance(sub, SettingsSubscriber)

    def test_watched_keys(self) -> None:
        sub, _, _ = _make_subscriber()
        assert sub.watched_keys == frozenset(
            {("security", "timeout_check_interval_seconds")},
        )

    def test_subscriber_name(self) -> None:
        sub, _, _ = _make_subscriber()
        assert sub.subscriber_name == "security-timeout-settings"


@pytest.mark.unit
class TestOnSettingsChanged:
    """on_settings_changed reschedules the scheduler on the watched key."""

    async def test_reschedules_on_watched_key(self) -> None:
        sub, scheduler, _ = _make_subscriber(interval_value="42.0")

        await sub.on_settings_changed("security", "timeout_check_interval_seconds")

        scheduler.reschedule.assert_called_once_with(42.0)

    async def test_unwatched_key_is_noop(self) -> None:
        sub, scheduler, settings_service = _make_subscriber()

        await sub.on_settings_changed("security", "audit_retention_days")

        settings_service.get.assert_not_awaited()
        scheduler.reschedule.assert_not_called()

    async def test_invalid_value_skipped(self) -> None:
        sub, scheduler, _ = _make_subscriber(interval_value="not-a-float")

        await sub.on_settings_changed("security", "timeout_check_interval_seconds")

        scheduler.reschedule.assert_not_called()

    async def test_settings_read_failure_skipped(self) -> None:
        sub, scheduler, _ = _make_subscriber(raise_on_get=True)

        await sub.on_settings_changed("security", "timeout_check_interval_seconds")

        scheduler.reschedule.assert_not_called()

    async def test_scheduler_value_error_swallowed(self) -> None:
        sub, scheduler, _ = _make_subscriber(
            interval_value="-1.0",
            reschedule_raises=True,
        )

        await sub.on_settings_changed("security", "timeout_check_interval_seconds")

        scheduler.reschedule.assert_called_once_with(-1.0)
