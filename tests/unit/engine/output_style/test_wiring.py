"""Unit tests for output-style settings-to-service wiring (hot-reload)."""

from collections.abc import Iterator
from types import SimpleNamespace
from typing import cast

import pytest

from synthorg.engine.output_style.provider import (
    current_house_style_provider,
    set_house_style_provider,
)
from synthorg.engine.output_style.service import (
    current_output_policy_service,
    set_output_policy_service,
)
from synthorg.engine.output_style.wiring import (
    build_output_style_config,
    rebuild_and_bind_output_style,
)
from synthorg.settings.service import SettingsService
from synthorg.settings.subscribers.output_style_subscriber import _WATCHED

_EXEMPTION = (
    '[{"rule_id": "emdash_literal", "scope_kind": "path", '
    '"match": "src/textfilter/**", "reason": "filter product"}]'
)


class _FakeSettings:
    """Minimal settings service exposing a batched namespace read."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    async def get_namespace(self, namespace: str) -> list[object]:
        assert namespace == "output_style"
        return [
            SimpleNamespace(definition=SimpleNamespace(key=key), value=value)
            for key, value in self._values.items()
        ]


def _settings(**overrides: str) -> SettingsService:
    values = {
        "enabled": "true",
        "shadow_mode": "false",
        "pack": "default",
        "house_style_enabled": "true",
        "exemptions": "[]",
    }
    values.update(overrides)
    return cast(SettingsService, _FakeSettings(values))


@pytest.fixture
def _reset_ambient() -> Iterator[None]:
    prev_service = current_output_policy_service()
    prev_provider = current_house_style_provider()
    try:
        yield
    finally:
        set_output_policy_service(prev_service)
        set_house_style_provider(prev_provider)


class TestBuildConfig:
    @pytest.mark.unit
    async def test_defaults(self) -> None:
        config = await build_output_style_config(_settings())
        assert config.enabled is True
        assert config.shadow_mode is False
        assert config.pack == "default"
        assert config.house_style_enabled is True
        assert config.exemptions == ()

    @pytest.mark.unit
    async def test_toggles_and_exemptions(self) -> None:
        config = await build_output_style_config(
            _settings(enabled="false", shadow_mode="true", exemptions=_EXEMPTION)
        )
        assert config.enabled is False
        assert config.shadow_mode is True
        assert len(config.exemptions) == 1
        assert config.exemptions[0].rule_id == "emdash_literal"

    @pytest.mark.unit
    async def test_malformed_exemptions_are_dropped(self) -> None:
        config = await build_output_style_config(_settings(exemptions="not json"))
        assert config.exemptions == ()


@pytest.mark.usefixtures("_reset_ambient")
class TestRebuildAndBind:
    @pytest.mark.unit
    async def test_binds_ambient_service_and_provider(self) -> None:
        set_output_policy_service(None)
        set_house_style_provider(None)
        service = await rebuild_and_bind_output_style(_settings())
        assert current_output_policy_service() is service
        assert current_house_style_provider() is not None

    @pytest.mark.unit
    async def test_unknown_pack_falls_back_to_default(self) -> None:
        service = await rebuild_and_bind_output_style(_settings(pack="does-not-exist"))
        assert service.pack.name == "default"
        assert current_output_policy_service() is service

    @pytest.mark.unit
    async def test_house_style_disabled_yields_no_directives(self) -> None:
        service = await rebuild_and_bind_output_style(
            _settings(house_style_enabled="false")
        )
        assert service.house_style_directives() == ()
        provider = current_house_style_provider()
        assert provider is not None
        assert provider.list_directives(role="Dev", department="Engineering") == ()


class TestSubscriberWatchedKeys:
    @pytest.mark.unit
    def test_watched_keys_cover_all_output_style_keys(self) -> None:
        # The subscriber only drives a rebuild for these keys; the rebuild +
        # rebind path itself is covered by TestRebuildAndBind and exercised
        # end to end (through the real dispatcher) by the API lifecycle tests.
        keys = {key for ns, key in _WATCHED if ns == "output_style"}
        assert keys == {
            "enabled",
            "shadow_mode",
            "pack",
            "house_style_enabled",
            "exemptions",
        }
