"""Tests for ``ObjectiveEntrySettingsSubscriber``.

An ``objectives.default_project`` change re-wires the objective entry adapter
(``wire_real_objective_entry(..., hot_swap=True)``) so the new slug takes effect
with no restart. Tests assert the re-wire fires on the watched key, no-ops on an
unexpected pair, and re-raises a wiring failure.
"""

from unittest.mock import create_autospec

import pytest

from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.engine.pipeline.entry import boot
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.objective_entry_subscriber import (
    ObjectiveEntrySettingsSubscriber,
)
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _make_subscriber() -> tuple[ObjectiveEntrySettingsSubscriber, AppState]:
    app_state = make_app_state(config=RootConfig(company_name="test"))
    sub = ObjectiveEntrySettingsSubscriber(
        app_state=app_state,
        settings_service=create_autospec(SettingsService, instance=True),
    )
    return sub, app_state


class TestProtocol:
    def test_isinstance(self) -> None:
        sub, _ = _make_subscriber()
        assert isinstance(sub, SettingsSubscriber)

    def test_watched_keys(self) -> None:
        sub, _ = _make_subscriber()
        assert sub.watched_keys == frozenset({("objectives", "default_project")})

    def test_subscriber_name(self) -> None:
        sub, _ = _make_subscriber()
        assert sub.subscriber_name == "objective-entry"


class TestRewire:
    async def test_watched_change_rewires_hot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = create_autospec(boot.wire_real_objective_entry)
        monkeypatch.setattr(boot, "wire_real_objective_entry", spy)
        sub, app_state = _make_subscriber()
        await sub.on_settings_changed("objectives", "default_project")
        spy.assert_awaited_once_with(app_state, hot_swap=True)

    async def test_unknown_key_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = create_autospec(boot.wire_real_objective_entry)
        monkeypatch.setattr(boot, "wire_real_objective_entry", spy)
        sub, _ = _make_subscriber()
        await sub.on_settings_changed("objectives", "unrelated")
        spy.assert_not_awaited()

    async def test_rewire_failure_reraises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = create_autospec(boot.wire_real_objective_entry)
        spy.side_effect = RuntimeError("wire boom")
        monkeypatch.setattr(boot, "wire_real_objective_entry", spy)
        sub, _ = _make_subscriber()
        with pytest.raises(RuntimeError, match="wire boom"):
            await sub.on_settings_changed("objectives", "default_project")
