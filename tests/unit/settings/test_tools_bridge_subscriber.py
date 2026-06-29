"""Tests for ``ToolsBridgeSettingsSubscriber``.

A change to a watched ``tools.docker_sidecar_*`` / ``docker_stop_grace_*`` key
re-resolves the ``ToolsBridgeConfig`` snapshot, swaps it onto
``app_state.bridge_config``, AND re-seeds the process sidecar-resolution cache
the sandbox reads per container launch. Tests assert both the live swap and the
cache re-seed, the resolver-failure path retains the prior snapshot, and an
unexpected pair no-ops.
"""

from unittest.mock import MagicMock, create_autospec

import pytest

import synthorg.settings.subscribers.tools_bridge_subscriber as tools_sub_mod
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.settings.bridge_configs import ToolsBridgeConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.tools_bridge_subscriber import (
    ToolsBridgeSettingsSubscriber,
)
from tests._shared import make_app_state

pytestmark = pytest.mark.unit

_WATCHED_KEY = "docker_sidecar_cpu_limit"


def _make_subscriber(
    *,
    snapshot: ToolsBridgeConfig | None = None,
    side_effect: BaseException | None = None,
) -> tuple[ToolsBridgeSettingsSubscriber, AppState]:
    resolver = create_autospec(ConfigResolver, instance=True)
    if side_effect is not None:
        resolver.get_tools_bridge_config.side_effect = side_effect
    else:
        resolver.get_tools_bridge_config.return_value = (
            snapshot if snapshot is not None else ToolsBridgeConfig()
        )
    app_state = make_app_state(
        config=RootConfig(company_name="test"),
        config_resolver=resolver,
    )
    sub = ToolsBridgeSettingsSubscriber(
        app_state=app_state,
        settings_service=create_autospec(SettingsService, instance=True),
    )
    return sub, app_state


class TestProtocol:
    def test_isinstance(self) -> None:
        sub, _ = _make_subscriber()
        assert isinstance(sub, SettingsSubscriber)

    def test_subscriber_name(self) -> None:
        sub, _ = _make_subscriber()
        assert sub.subscriber_name == "tools-bridge-config"

    def test_watched_keys_cover_sidecar_and_stop_grace(self) -> None:
        sub, _ = _make_subscriber()
        watched = sub.watched_keys
        assert ("tools", "docker_sidecar_cpu_limit") in watched
        assert ("tools", "docker_stop_grace_timeout_seconds") in watched


class TestApply:
    async def test_change_swaps_snapshot_and_reseeds_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache_spy = MagicMock()
        monkeypatch.setattr(tools_sub_mod, "set_resolved_sidecar_limits", cache_spy)
        snapshot = ToolsBridgeConfig(docker_sidecar_cpu_limit=1.5)
        sub, app_state = _make_subscriber(snapshot=snapshot)

        await sub.on_settings_changed("tools", _WATCHED_KEY)

        assert app_state.bridge_config.tools is snapshot
        cache_spy.assert_called_once_with(snapshot)

    async def test_resolver_failure_retains_prior_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache_spy = MagicMock()
        monkeypatch.setattr(tools_sub_mod, "set_resolved_sidecar_limits", cache_spy)
        sub, app_state = _make_subscriber(side_effect=RuntimeError("resolver outage"))
        prior = app_state.bridge_config.tools

        with pytest.raises(RuntimeError, match="resolver outage"):
            await sub.on_settings_changed("tools", _WATCHED_KEY)

        assert app_state.bridge_config.tools is prior
        cache_spy.assert_not_called()

    async def test_unknown_key_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cache_spy = MagicMock()
        monkeypatch.setattr(tools_sub_mod, "set_resolved_sidecar_limits", cache_spy)
        sub, app_state = _make_subscriber()
        prior = app_state.bridge_config.tools
        await sub.on_settings_changed("tools", "unrelated")
        assert app_state.bridge_config.tools is prior
        cache_spy.assert_not_called()
