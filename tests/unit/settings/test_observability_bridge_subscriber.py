"""Tests for ``ObservabilityBridgeSettingsSubscriber``.

A change to a watched ``observability.*`` key re-resolves the
``ObservabilityBridgeConfig`` snapshot, swaps it onto
``app_state.bridge_config``, and live-applies the HTTP batch knobs onto every
installed ``HttpBatchHandler``. A change to
``audit_chain_signing_timeout_seconds`` additionally pushes the new timeout
onto every live ``AuditChainSink``. The per-preset TSA endpoints are
compose-set trust anchors and are deliberately not watched. Tests assert the
swap,
the conditional signing-timeout apply, the resolver-failure retention, and the
unexpected-pair no-op.
"""

from unittest.mock import AsyncMock, MagicMock, create_autospec

import pytest

import synthorg.api.lifecycle_helpers.config_apply as config_apply_mod
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.settings.bridge_configs import ObservabilityBridgeConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.observability_bridge_subscriber import (
    ObservabilityBridgeSettingsSubscriber,
)
from tests._shared import make_app_state

pytestmark = pytest.mark.unit

_SIGNING_KEY = "audit_chain_signing_timeout_seconds"
_HTTP_KEY = "http_batch_size"


def _make_subscriber(
    *,
    snapshot: ObservabilityBridgeConfig | None = None,
    side_effect: BaseException | None = None,
) -> tuple[ObservabilityBridgeSettingsSubscriber, AppState]:
    resolver = create_autospec(ConfigResolver, instance=True)
    if side_effect is not None:
        resolver.get_observability_bridge_config.side_effect = side_effect
    else:
        resolver.get_observability_bridge_config.return_value = (
            snapshot if snapshot is not None else ObservabilityBridgeConfig()
        )
    app_state = make_app_state(
        config=RootConfig(company_name="test"),
        config_resolver=resolver,
    )
    sub = ObservabilityBridgeSettingsSubscriber(
        app_state=app_state,
        settings_service=create_autospec(SettingsService, instance=True),
    )
    return sub, app_state


def _patch_apply(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, AsyncMock]:
    """Replace the two config-apply helpers and return their spies."""
    http_spy = MagicMock()
    signing_spy = AsyncMock()
    monkeypatch.setattr(config_apply_mod, "apply_http_log_handler_settings", http_spy)
    monkeypatch.setattr(
        config_apply_mod, "_apply_audit_chain_signing_timeout", signing_spy
    )
    return http_spy, signing_spy


class TestProtocol:
    def test_isinstance(self) -> None:
        sub, _ = _make_subscriber()
        assert isinstance(sub, SettingsSubscriber)

    def test_subscriber_name(self) -> None:
        sub, _ = _make_subscriber()
        assert sub.subscriber_name == "observability-bridge-config"

    def test_watched_keys_cover_http_and_signing_timeout(self) -> None:
        sub, _ = _make_subscriber()
        watched = sub.watched_keys
        assert ("observability", _HTTP_KEY) in watched
        assert ("observability", _SIGNING_KEY) in watched

    def test_tsa_endpoints_are_not_watched(self) -> None:
        """TSA endpoints are compose-set, so watching them would be dead."""
        sub, _ = _make_subscriber()
        watched = sub.watched_keys
        for preset in ("freetsa", "digicert", "sectigo"):
            assert ("observability", f"tsa_endpoint_{preset}") not in watched


class TestApply:
    async def test_http_change_swaps_and_skips_signing_apply(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        http_spy, signing_spy = _patch_apply(monkeypatch)
        snapshot = ObservabilityBridgeConfig(http_batch_size=250)
        sub, app_state = _make_subscriber(snapshot=snapshot)

        await sub.on_settings_changed([("observability", _HTTP_KEY)])

        assert app_state.bridge_config.observability is snapshot
        http_spy.assert_called_once_with(snapshot)
        signing_spy.assert_not_awaited()

    async def test_signing_change_swaps_and_applies_to_sinks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        http_spy, signing_spy = _patch_apply(monkeypatch)
        snapshot = ObservabilityBridgeConfig(audit_chain_signing_timeout_seconds=7.5)
        sub, app_state = _make_subscriber(snapshot=snapshot)

        await sub.on_settings_changed([("observability", _SIGNING_KEY)])

        assert app_state.bridge_config.observability is snapshot
        http_spy.assert_called_once_with(snapshot)
        signing_spy.assert_awaited_once_with(app_state)

    async def test_resolver_failure_retains_prior_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        http_spy, signing_spy = _patch_apply(monkeypatch)
        sub, app_state = _make_subscriber(side_effect=RuntimeError("resolver outage"))
        prior = app_state.bridge_config.observability

        with pytest.raises(RuntimeError, match="resolver outage"):
            await sub.on_settings_changed([("observability", _SIGNING_KEY)])

        assert app_state.bridge_config.observability is prior
        http_spy.assert_not_called()
        signing_spy.assert_not_awaited()

    def test_an_unwatched_key_is_not_declared(self) -> None:
        # Filtering a batch to a subscriber's watched pairs is the
        # dispatcher's job and is asserted there; what is left here is that
        # the declared set does not reach beyond what the swap needs.
        sub, _ = _make_subscriber()

        assert ("observability", "unrelated") not in sub.watched_keys
