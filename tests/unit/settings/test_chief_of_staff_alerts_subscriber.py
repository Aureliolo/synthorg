"""Coverage for the Chief-of-Staff alerts settings subscriber.

The subscriber starts/stops the org-inflection monitor live to match the
effective alerts capability (the persona master switch AND
``chief_of_staff.alerts_enabled``).
"""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from synthorg.meta.chief_of_staff.monitor import OrgInflectionMonitor
from synthorg.meta.state import MetaStateSlice
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.state import SettingsStateSlice
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.chief_of_staff_alerts_subscriber import (
    ChiefOfStaffAlertsSettingsSubscriber,
)
from tests._shared import make_app_state, mock_of
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.unit


@pytest.fixture
async def settings() -> AsyncIterator[SettingsService]:
    backend = FakePersistenceBackend()
    await backend.connect()
    yield SettingsService(repository=backend.settings, registry=get_registry())
    await backend.disconnect()


def _wire(
    settings: SettingsService, monitor: object
) -> ChiefOfStaffAlertsSettingsSubscriber:
    from synthorg.config.schema import RootConfig

    resolver = ConfigResolver(
        settings_service=settings, config=RootConfig(company_name="test")
    )
    app_state = make_app_state(
        slices={
            MetaStateSlice: {"org_inflection_monitor": monitor},
            SettingsStateSlice: {"config_resolver": resolver},
        },
    )
    return ChiefOfStaffAlertsSettingsSubscriber(
        app_state=app_state, settings_service=settings
    )


def test_is_a_settings_subscriber(settings: SettingsService) -> None:
    sub = ChiefOfStaffAlertsSettingsSubscriber(
        app_state=make_app_state(), settings_service=settings
    )
    assert isinstance(sub, SettingsSubscriber)
    assert ("chief_of_staff", "alerts_enabled") in sub.watched_keys
    assert ("self_improvement", "chief_of_staff_enabled") in sub.watched_keys


async def test_enable_starts_the_monitor(settings: SettingsService) -> None:
    await settings.set("self_improvement", "chief_of_staff_enabled", "true")
    await settings.set("chief_of_staff", "alerts_enabled", "true")
    monitor = mock_of[OrgInflectionMonitor](start=AsyncMock(), stop=AsyncMock())
    sub = _wire(settings, monitor)

    await sub.on_settings_changed("chief_of_staff", "alerts_enabled")

    monitor.start.assert_awaited_once()
    monitor.stop.assert_not_awaited()


async def test_disable_stops_the_monitor(settings: SettingsService) -> None:
    await settings.set("self_improvement", "chief_of_staff_enabled", "true")
    await settings.set("chief_of_staff", "alerts_enabled", "false")
    monitor = mock_of[OrgInflectionMonitor](start=AsyncMock(), stop=AsyncMock())
    sub = _wire(settings, monitor)

    await sub.on_settings_changed("chief_of_staff", "alerts_enabled")

    monitor.stop.assert_awaited_once()
    monitor.start.assert_not_awaited()


async def test_master_off_stops_the_monitor(settings: SettingsService) -> None:
    """The persona master switch off stops alerts even when its flag is on."""
    await settings.set("self_improvement", "chief_of_staff_enabled", "false")
    await settings.set("chief_of_staff", "alerts_enabled", "true")
    monitor = mock_of[OrgInflectionMonitor](start=AsyncMock(), stop=AsyncMock())
    sub = _wire(settings, monitor)

    await sub.on_settings_changed("self_improvement", "chief_of_staff_enabled")

    monitor.stop.assert_awaited_once()
    monitor.start.assert_not_awaited()
