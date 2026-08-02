"""Settings-to-provider wiring for the ask policy, including its failure posture."""

from types import SimpleNamespace
from typing import cast

import pytest

from synthorg.engine.ask_policy.provider import (
    current_ask_policy_provider,
    set_ask_policy_provider,
)
from synthorg.engine.ask_policy.wiring import (
    build_ask_policy_config,
    rebuild_and_bind_ask_policy,
)
from synthorg.settings.service import SettingsService
from synthorg.settings.subscribers.ask_policy_subscriber import _WATCHED

_EXTRAS = (
    '[{"id": "x_eng", "text": "Ask before a schema change.", '
    '"scope": "Engineering", "scope_kind": "department"}]'
)


class _FakeSettings:
    """Minimal settings service exposing the two targeted key reads."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    async def get(self, namespace: str, key: str) -> object:
        assert namespace == "engine"
        return SimpleNamespace(value=self._values.get(key))


class _FailingSettings:
    """Settings service whose reads fail for a recoverable reason."""

    async def get(self, namespace: str, key: str) -> object:
        msg = f"settings backend unavailable for {namespace}/{key}"
        raise OSError(msg)


def _settings(**overrides: str) -> SettingsService:
    values = {"ask_policy_enabled": "true", "ask_policy_extra_directives": "[]"}
    values.update(overrides)
    return cast("SettingsService", _FakeSettings(values))


class TestBuildConfig:
    @pytest.mark.unit
    async def test_defaults(self) -> None:
        config = await build_ask_policy_config(_settings())
        assert config.enabled is True
        assert config.extra_directives == ()

    @pytest.mark.unit
    async def test_disabled(self) -> None:
        config = await build_ask_policy_config(_settings(ask_policy_enabled="false"))
        assert config.enabled is False

    @pytest.mark.unit
    async def test_extra_directives_parse(self) -> None:
        config = await build_ask_policy_config(
            _settings(ask_policy_extra_directives=_EXTRAS)
        )
        assert [d.id for d in config.extra_directives] == ["x_eng"]

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "raw",
        ["not json", '{"id": "x"}', '[{"id": "x"}]', '[{"id": "", "text": "y"}]'],
    )
    async def test_malformed_extras_fail_safe_to_none(self, raw: str) -> None:
        config = await build_ask_policy_config(
            _settings(ask_policy_extra_directives=raw)
        )
        assert config.extra_directives == ()
        assert config.enabled is True


class TestRebuildAndBind:
    @pytest.mark.unit
    async def test_binds_the_ambient_provider(self) -> None:
        set_ask_policy_provider(None)
        await rebuild_and_bind_ask_policy(_settings())
        assert current_ask_policy_provider() is not None

    @pytest.mark.unit
    async def test_rebind_replaces_the_previous_provider(self) -> None:
        await rebuild_and_bind_ask_policy(_settings())
        first = current_ask_policy_provider()
        await rebuild_and_bind_ask_policy(_settings())
        assert current_ask_policy_provider() is not first

    @pytest.mark.unit
    async def test_disabled_setting_binds_a_disabled_provider(self) -> None:
        await rebuild_and_bind_ask_policy(_settings(ask_policy_enabled="false"))
        provider = current_ask_policy_provider()
        assert provider is not None
        assert provider.enabled is False

    @pytest.mark.unit
    async def test_settings_failure_still_binds_an_enabled_provider(self) -> None:
        # Fail to ON: for enforcement the conservative direction is to keep
        # enforcing, and for asking it is to keep asking.
        set_ask_policy_provider(None)
        await rebuild_and_bind_ask_policy(cast("SettingsService", _FailingSettings()))
        provider = current_ask_policy_provider()
        assert provider is not None
        assert provider.enabled is True


class TestSubscriberWatchedKeys:
    @pytest.mark.unit
    def test_watches_only_the_two_ask_policy_keys(self) -> None:
        assert (
            frozenset(
                {
                    ("engine", "ask_policy_enabled"),
                    ("engine", "ask_policy_extra_directives"),
                }
            )
            == _WATCHED
        )
