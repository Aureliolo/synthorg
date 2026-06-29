"""Tests for ``SecurityBridgeSettingsSubscriber`` (Phase F).

Proves the security-toggle hot-reload chain: a watched ``security.*`` change
re-resolves the four toggles, rebuilds ``SecurityConfig``, and swaps it into the
``app_state.security_runtime_config`` holder the per-request interceptor reads.
The weakening guardrail lives at the write path (see test_write_governance);
this subscriber is purely mechanical, so the tests assert the live swap, the
preservation of unrelated security fields, fail-safe on resolver outage (prior,
more-secure config retained), and unexpected-pair no-op.
"""

from unittest.mock import create_autospec

import pytest

from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.security_bridge_subscriber import (
    SecurityBridgeSettingsSubscriber,
)
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _make_subscriber(
    *,
    enabled: bool = True,
    audit_enabled: bool = True,
    post_tool_scanning_enabled: bool = True,
    output_scan_policy_type: str = "autonomy_tiered",
    bool_side_effect: BaseException | None = None,
) -> tuple[SecurityBridgeSettingsSubscriber, AppState]:
    """Build the subscriber over a real AppState + spec'd resolver."""
    resolver = create_autospec(ConfigResolver, instance=True)
    if bool_side_effect is not None:
        resolver.get_bool.side_effect = bool_side_effect
    else:

        async def _get_bool(namespace: str, key: str) -> bool:
            del namespace
            return {
                "enabled": enabled,
                "audit_enabled": audit_enabled,
                "post_tool_scanning_enabled": post_tool_scanning_enabled,
            }[key]

        resolver.get_bool.side_effect = _get_bool
    resolver.get_str.return_value = output_scan_policy_type
    app_state = make_app_state(
        config=RootConfig(company_name="test"),
        config_resolver=resolver,
    )
    sub = SecurityBridgeSettingsSubscriber(
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
        assert sub.watched_keys == frozenset(
            {
                ("security", "enabled"),
                ("security", "audit_enabled"),
                ("security", "post_tool_scanning_enabled"),
                ("security", "output_scan_policy_type"),
            }
        )

    def test_subscriber_name(self) -> None:
        sub, _ = _make_subscriber()
        assert sub.subscriber_name == "security-bridge-config"


class TestSwap:
    async def test_disable_applies_to_live_holder(self) -> None:
        sub, app_state = _make_subscriber(enabled=False)
        before = app_state.security_runtime_config.current
        assert before is not None
        assert before.enabled is True

        await sub.on_settings_changed("security", "enabled")

        live = app_state.security_runtime_config.current
        assert live is not None
        assert live.enabled is False
        # Unrelated security fields are preserved from the boot config.
        assert live.rule_engine == app_state.config.security.rule_engine

    async def test_output_scan_policy_applies_live(self) -> None:
        sub, app_state = _make_subscriber(output_scan_policy_type="log_only")
        await sub.on_settings_changed("security", "output_scan_policy_type")
        live = app_state.security_runtime_config.current
        assert live is not None
        assert live.output_scan_policy_type == "log_only"

    async def test_resolver_failure_retains_prior_secure_config(self) -> None:
        sub, app_state = _make_subscriber(
            bool_side_effect=RuntimeError("resolver outage"),
        )
        prior = app_state.security_runtime_config.current
        with pytest.raises(RuntimeError, match="resolver outage"):
            await sub.on_settings_changed("security", "enabled")
        # The prior (more secure) config stays in place; no partial swap.
        assert app_state.security_runtime_config.current is prior

    async def test_unknown_key_is_noop(self) -> None:
        sub, app_state = _make_subscriber(enabled=False)
        prior = app_state.security_runtime_config.current
        await sub.on_settings_changed("security", "unrelated")
        assert app_state.security_runtime_config.current is prior
