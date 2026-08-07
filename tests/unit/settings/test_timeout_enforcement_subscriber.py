"""Tests for ``EngineTimeoutEnforcementSettingsSubscriber``.

Proves the kill-switch hot-reload: a ``engine.timeout_enforcement_enabled``
change resolves the flag and pushes it into the process cache the engine reads
per coroutine entry. Critically, a resolver outage must fail SAFE (force the
cache back ON), never silently disable enforcement.
"""

from unittest.mock import MagicMock, create_autospec

import pytest

import synthorg.engine.timeout_enforcement as timeout_mod
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.timeout_enforcement_subscriber import (
    EngineTimeoutEnforcementSettingsSubscriber,
)
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _make_subscriber(
    *,
    bool_return: bool = False,
    bool_side_effect: BaseException | None = None,
) -> EngineTimeoutEnforcementSettingsSubscriber:
    resolver = create_autospec(ConfigResolver, instance=True)
    if bool_side_effect is not None:
        resolver.get_bool.side_effect = bool_side_effect
    else:
        resolver.get_bool.return_value = bool_return
    app_state: AppState = make_app_state(
        config=RootConfig(company_name="test"),
        config_resolver=resolver,
    )
    return EngineTimeoutEnforcementSettingsSubscriber(
        app_state=app_state,
        settings_service=create_autospec(SettingsService, instance=True),
    )


class TestProtocol:
    def test_isinstance(self) -> None:
        assert isinstance(_make_subscriber(), SettingsSubscriber)

    def test_watched_keys(self) -> None:
        assert _make_subscriber().watched_keys == frozenset(
            {("engine", "timeout_enforcement_enabled")}
        )

    def test_subscriber_name(self) -> None:
        assert _make_subscriber().subscriber_name == "engine-timeout-enforcement"


class TestApply:
    async def test_disable_pushes_false_into_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = MagicMock()
        monkeypatch.setattr(timeout_mod, "set_timeout_enforcement_enabled", spy)
        sub = _make_subscriber(bool_return=False)
        await sub.on_settings_changed([("engine", "timeout_enforcement_enabled")])
        spy.assert_called_once_with(value=False)

    async def test_resolver_failure_forces_enabled_and_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = MagicMock()
        monkeypatch.setattr(timeout_mod, "set_timeout_enforcement_enabled", spy)
        sub = _make_subscriber(bool_side_effect=RuntimeError("resolver outage"))
        with pytest.raises(RuntimeError, match="resolver outage"):
            await sub.on_settings_changed([("engine", "timeout_enforcement_enabled")])
        # Fail-safe: enforcement is forced back ON, never left disabled.
        spy.assert_called_once_with(value=True)

    async def test_unknown_key_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = MagicMock()
        monkeypatch.setattr(timeout_mod, "set_timeout_enforcement_enabled", spy)
        sub = _make_subscriber()
        await sub.on_settings_changed([("engine", "unrelated")])
        spy.assert_not_called()
