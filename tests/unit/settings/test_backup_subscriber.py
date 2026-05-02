"""Tests for BackupSettingsSubscriber."""

from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from synthorg.backup.scheduler import BackupScheduler
from synthorg.backup.service import BackupService
from synthorg.settings.models import SettingValue
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.backup_subscriber import (
    BackupSettingsSubscriber,
)


def _make_subscriber(
    *,
    scheduler_running: bool = False,
    enabled: bool = True,
    schedule_hours: int = 6,
) -> tuple[BackupSettingsSubscriber, MagicMock]:
    """Create a subscriber with a mock BackupService and SettingsService.

    Args:
        scheduler_running: Initial ``is_running`` state of the mock scheduler.
        enabled: Value returned by settings_service.get("backup", "enabled").
        schedule_hours: Value returned by
            settings_service.get("backup", "schedule_hours").

    Returns:
        Tuple of (subscriber, mock_backup_service).
    """
    scheduler = MagicMock(spec=BackupScheduler)
    type(scheduler).is_running = PropertyMock(return_value=scheduler_running)
    scheduler.start = AsyncMock(spec=BackupScheduler.start)
    scheduler.stop = AsyncMock(spec=BackupScheduler.stop)
    scheduler.reschedule = MagicMock(spec=BackupScheduler.reschedule)

    service = MagicMock(spec=BackupService)
    type(service).scheduler = PropertyMock(return_value=scheduler)

    settings_service = MagicMock(spec=SettingsService)

    async def _mock_get(namespace: str, key: str) -> MagicMock:
        del namespace
        result = MagicMock(spec=SettingValue)
        if key == "enabled":
            result.value = str(enabled)
        elif key == "schedule_hours":
            result.value = str(schedule_hours)
        else:
            result.value = ""
        return result

    settings_service.get = AsyncMock(spec=SettingsService.get, side_effect=_mock_get)

    sub = BackupSettingsSubscriber(
        backup_service=service,
        settings_service=settings_service,
    )
    return sub, service


@pytest.mark.unit
class TestBackupSubscriberProtocol:
    """BackupSettingsSubscriber conforms to SettingsSubscriber."""

    def test_isinstance_check(self) -> None:
        sub, _ = _make_subscriber()
        assert isinstance(sub, SettingsSubscriber)

    def test_watched_keys_returns_expected_frozenset(self) -> None:
        sub, _ = _make_subscriber()
        expected = frozenset(
            {
                ("backup", "enabled"),
                ("backup", "schedule_hours"),
                ("backup", "compression"),
                ("backup", "on_shutdown"),
                ("backup", "on_startup"),
            }
        )
        assert sub.watched_keys == expected

    def test_subscriber_name(self) -> None:
        sub, _ = _make_subscriber()
        assert sub.subscriber_name == "backup-settings"


@pytest.mark.unit
class TestBackupSubscriberEnabled:
    """on_settings_changed('backup', 'enabled') toggles the scheduler."""

    async def test_enabled_starts_scheduler_when_stopped(self) -> None:
        sub, service = _make_subscriber(
            scheduler_running=False,
            enabled=True,
        )

        await sub.on_settings_changed("backup", "enabled")

        # ``assert_awaited_once`` (vs ``assert_called_once``) catches a
        # regression where the start coroutine is created but never
        # awaited -- the call would still be recorded but the
        # scheduler would never actually launch.
        service.scheduler.start.assert_awaited_once()
        service.scheduler.stop.assert_not_awaited()

    async def test_enabled_stops_scheduler_when_running(self) -> None:
        sub, service = _make_subscriber(
            scheduler_running=True,
            enabled=False,
        )

        await sub.on_settings_changed("backup", "enabled")

        service.scheduler.stop.assert_awaited_once()
        service.scheduler.start.assert_not_called()

    async def test_enabled_toggle_is_idempotent(self) -> None:
        """Calling twice with same state does not error."""
        sub, service = _make_subscriber(
            scheduler_running=False,
            enabled=True,
        )
        await sub.on_settings_changed("backup", "enabled")
        await sub.on_settings_changed("backup", "enabled")
        # Two start() calls -- no crash, idempotent
        assert service.scheduler.start.await_count == 2


@pytest.mark.unit
class TestBackupSubscriberAdvisory:
    """Advisory keys log info but do not touch the scheduler."""

    @pytest.mark.parametrize(
        "key",
        [
            "compression",
            "on_shutdown",
            "on_startup",
        ],
    )
    async def test_advisory_key_does_not_start_scheduler(
        self,
        key: str,
    ) -> None:
        sub, service = _make_subscriber(scheduler_running=False)

        await sub.on_settings_changed("backup", key)

        service.scheduler.start.assert_not_called()
        service.scheduler.stop.assert_not_awaited()

    async def test_schedule_hours_reschedules_without_toggle(self) -> None:
        """schedule_hours calls reschedule but does not stop/start scheduler."""
        sub, service = _make_subscriber(scheduler_running=True)

        await sub.on_settings_changed("backup", "schedule_hours")

        service.scheduler.start.assert_not_called()
        service.scheduler.stop.assert_not_awaited()
        service.scheduler.reschedule.assert_called_once()

    @pytest.mark.parametrize(
        "key",
        ["compression", "on_shutdown", "on_startup"],
    )
    async def test_advisory_keys_do_not_raise(self, key: str) -> None:
        sub, _ = _make_subscriber()
        # Should complete without error -- just logs INFO
        await sub.on_settings_changed("backup", key)
