"""Tests for SimulationsSettingsSubscriber."""

from unittest.mock import AsyncMock

import pytest

from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.simulations_subscriber import (
    SimulationsSettingsSubscriber,
)
from tests._shared import make_app_state

pytestmark = pytest.mark.unit

_RELOAD_TARGET = "synthorg.workers.runtime_builder.reload_runtime_services"


def _make_state() -> AppState:
    return make_app_state(config=RootConfig(company_name="test"))


def _make_subscriber(state: AppState) -> SimulationsSettingsSubscriber:
    return SimulationsSettingsSubscriber(app_state=state)


class TestProtocol:
    """Structural + metadata conformance."""

    def test_isinstance(self) -> None:
        assert isinstance(_make_subscriber(_make_state()), SettingsSubscriber)

    def test_subscriber_name(self) -> None:
        assert _make_subscriber(_make_state()).subscriber_name == "simulations-settings"

    def test_watches_intake_and_review_keys(self) -> None:
        watched = _make_subscriber(_make_state()).watched_keys
        for key in (
            "intake_strategy",
            "intake_model",
            "intake_default_project",
            "review_pipeline_strategy",
        ):
            assert ("simulations", key) in watched


class TestRebuild:
    """on_settings_changed routes through the full runtime reload."""

    async def test_change_triggers_runtime_reload(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        state = _make_state()
        reload = AsyncMock()
        monkeypatch.setattr(_RELOAD_TARGET, reload)
        sub = _make_subscriber(state)
        await sub.on_settings_changed("simulations", "intake_strategy")
        reload.assert_awaited_once_with(state)

    async def test_reload_failure_propagates(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        state = _make_state()
        reload = AsyncMock(side_effect=RuntimeError("reload boom"))
        monkeypatch.setattr(_RELOAD_TARGET, reload)
        sub = _make_subscriber(state)
        with pytest.raises(RuntimeError, match="reload boom"):
            await sub.on_settings_changed("simulations", "review_pipeline_strategy")
