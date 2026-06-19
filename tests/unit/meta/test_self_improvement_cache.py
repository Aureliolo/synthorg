"""Tests for the cached ``self_improvement_config_of`` accessor.

Pins the dedup contract: the parsed config is loaded once and cached on
the meta slice; a subscriber invalidation (wiring the field back to
``None``) makes the next read reload, preserving hot-reload.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.state import MetaStateSlice, self_improvement_config_of
from synthorg.settings.service import SettingsService
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _service_with_probe() -> tuple[SettingsService, AsyncMock]:
    """Return a settings-service double and its ``get`` probe.

    ``meta.self_improvement`` resolves to ``{}`` (code defaults). The
    loader only reads ``entry.value``, so an attribute-bag suffices.
    """
    entry = SimpleNamespace(value="{}")
    get_mock = AsyncMock(return_value=entry)
    service = mock_of[SettingsService](get=get_mock)
    return service, get_mock


async def test_config_loaded_once_and_cached() -> None:
    """Two reads return the same instance and hit the backend once."""
    service, get_mock = _service_with_probe()
    app_state = make_app_state(settings_service=service)

    first = await self_improvement_config_of(app_state)
    second = await self_improvement_config_of(app_state)

    assert isinstance(first, SelfImprovementConfig)
    assert first is second
    get_mock.assert_awaited_once_with("meta", "self_improvement")


async def test_invalidation_reloads_config() -> None:
    """Wiring the field back to ``None`` forces a fresh load on next read."""
    service, get_mock = _service_with_probe()
    app_state = make_app_state(settings_service=service)

    await self_improvement_config_of(app_state)
    app_state.wire(MetaStateSlice, self_improvement_config=None)
    await self_improvement_config_of(app_state)

    assert get_mock.await_count == 2
