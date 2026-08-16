"""Tests for ``SecurityBridgeSettingsSubscriber`` (Phase F).

Proves the security-toggle hot-reload chain: a watched ``security.*`` change
re-resolves the toggles, rebuilds ``SecurityConfig``, and swaps it into the
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
from synthorg.security.config import McpSelfConsumerMode
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
    mcp_self_consumer_mode: str = "disabled",
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

    async def _get_str(namespace: str, key: str) -> str:
        del namespace
        return {
            "output_scan_policy_type": output_scan_policy_type,
            "mcp_self_consumer_mode": mcp_self_consumer_mode,
        }[key]

    resolver.get_str.side_effect = _get_str
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
                ("security", "mcp_self_consumer_mode"),
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

        await sub.on_settings_changed([("security", "enabled")])

        live = app_state.security_runtime_config.current
        assert live is not None
        assert live.enabled is False
        # Unrelated security fields are preserved from the boot config.
        assert live.rule_engine == app_state.config.security.rule_engine

    async def test_output_scan_policy_applies_live(self) -> None:
        sub, app_state = _make_subscriber(output_scan_policy_type="log_only")
        await sub.on_settings_changed([("security", "output_scan_policy_type")])
        live = app_state.security_runtime_config.current
        assert live is not None
        assert live.output_scan_policy_type == "log_only"

    async def test_opening_the_agent_mcp_bridge_applies_live(self) -> None:
        # The other half of ``chief_of_staff.direct_mcp_enabled``: with the
        # bridge closed that opt-in can never materialise, so an operator who
        # flips it reads a permanently blocked subsystem.
        sub, app_state = _make_subscriber(mcp_self_consumer_mode="trust_scoped")

        await sub.on_settings_changed([("security", "mcp_self_consumer_mode")])

        live = app_state.security_runtime_config.current
        assert live is not None
        assert live.mcp_self_consumer.mode is McpSelfConsumerMode.TRUST_SCOPED

    async def test_the_rest_of_the_bridge_block_is_carried_forward(self) -> None:
        # Every other field on the block is compose-time, so replacing the
        # whole block would silently discard an operator's rate limits and
        # allowlists on a mode change.
        sub, app_state = _make_subscriber(mcp_self_consumer_mode="trust_scoped")
        boot = app_state.config.security.mcp_self_consumer

        await sub.on_settings_changed([("security", "mcp_self_consumer_mode")])

        live = app_state.security_runtime_config.current
        assert live is not None
        assert live.mcp_self_consumer.denied_tools == boot.denied_tools
        assert (
            live.mcp_self_consumer.rate_limit_per_minute == boot.rate_limit_per_minute
        )

    async def test_resolver_failure_retains_prior_secure_config(self) -> None:
        sub, app_state = _make_subscriber(
            bool_side_effect=RuntimeError("resolver outage"),
        )
        prior = app_state.security_runtime_config.current
        with pytest.raises(RuntimeError, match="resolver outage"):
            await sub.on_settings_changed([("security", "enabled")])
        # The prior (more secure) config stays in place; no partial swap.
        assert app_state.security_runtime_config.current is prior

    def test_an_unwatched_key_is_not_declared(self) -> None:
        # Filtering a batch to a subscriber's watched pairs is the
        # dispatcher's job and is asserted there; what is left here is that
        # the declared set does not reach beyond the toggles it rebuilds.
        sub, _ = _make_subscriber(enabled=False)
        assert ("security", "unrelated") not in sub.watched_keys
