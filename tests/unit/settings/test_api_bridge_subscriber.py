"""Tests for ``ApiBridgeSettingsSubscriber``.

The subscriber hot-swaps ``app_state.api_bridge_config`` when the
operator changes a watched API setting. Tests cover protocol
conformance, happy-path swap (single field updated via
``model_copy``, every other field preserved), unexpected key/namespace
no-op, and resolver-failure path (no swap, error re-raised so the
dispatcher logs subscriber context).
"""

from unittest.mock import create_autospec

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.settings.bridge_configs import ApiBridgeConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.api_bridge_subscriber import (
    ApiBridgeSettingsSubscriber,
)

pytestmark = pytest.mark.unit


def _make_subscriber(
    *,
    snapshot: ApiBridgeConfig | None = None,
    resolver_int_return: int | None = None,
    resolver_int_side_effect: BaseException | None = None,
) -> tuple[ApiBridgeSettingsSubscriber, AppState]:
    """Build a subscriber with a real AppState + spec'd ConfigResolver.

    Returns the subscriber plus the AppState so callers can assert
    on the post-call ``api_bridge_config`` snapshot.
    """
    settings_service = create_autospec(SettingsService, instance=True)

    resolver = create_autospec(ConfigResolver, instance=True)
    if resolver_int_side_effect is not None:
        resolver.get_int.side_effect = resolver_int_side_effect
    else:
        resolver.get_int.return_value = resolver_int_return

    app_state = AppState(
        config=RootConfig(company_name="test"),
        approval_store=ApprovalStore(),
    )
    app_state._config_resolver = resolver
    if snapshot is not None:
        app_state.swap_api_bridge_config(snapshot)

    sub = ApiBridgeSettingsSubscriber(
        app_state=app_state,
        settings_service=settings_service,
    )
    return sub, app_state


class TestSubscriberProtocol:
    """``ApiBridgeSettingsSubscriber`` conforms to ``SettingsSubscriber``."""

    def test_isinstance_check(self) -> None:
        sub, _ = _make_subscriber(resolver_int_return=10_000)
        assert isinstance(sub, SettingsSubscriber)

    def test_watched_keys(self) -> None:
        sub, _ = _make_subscriber(resolver_int_return=10_000)
        assert sub.watched_keys == frozenset(
            {("api", "max_lifecycle_events_per_query")}
        )

    def test_subscriber_name(self) -> None:
        sub, _ = _make_subscriber(resolver_int_return=10_000)
        assert sub.subscriber_name == "api-bridge-config"


class TestRebuild:
    """``on_settings_changed`` rebuilds + swaps the snapshot."""

    async def test_lifecycle_cap_change_swaps_with_model_copy(self) -> None:
        original = ApiBridgeConfig(
            max_lifecycle_events_per_query=10_000,
            max_audit_records_per_query=42_000,
        )
        sub, app_state = _make_subscriber(
            snapshot=original,
            resolver_int_return=50_000,
        )

        await sub.on_settings_changed("api", "max_lifecycle_events_per_query")

        swapped = app_state.api_bridge_config
        assert swapped.max_lifecycle_events_per_query == 50_000
        # Every other field is preserved verbatim from the prior snapshot.
        assert swapped.max_audit_records_per_query == 42_000

    async def test_resolver_failure_does_not_swap(self) -> None:
        original = ApiBridgeConfig(max_lifecycle_events_per_query=8_000)
        sub, app_state = _make_subscriber(
            snapshot=original,
            resolver_int_side_effect=RuntimeError("resolver outage"),
        )

        with pytest.raises(RuntimeError, match="resolver outage"):
            await sub.on_settings_changed("api", "max_lifecycle_events_per_query")

        # Snapshot retained from before the change.
        assert app_state.api_bridge_config is original
        assert app_state.api_bridge_config.max_lifecycle_events_per_query == 8_000

    async def test_memory_error_propagates(self) -> None:
        sub, app_state = _make_subscriber(
            resolver_int_side_effect=MemoryError(),
        )
        before = app_state.api_bridge_config

        with pytest.raises(MemoryError):
            await sub.on_settings_changed("api", "max_lifecycle_events_per_query")

        assert app_state.api_bridge_config is before


class TestUnexpectedRouting:
    """Unexpected (namespace, key) pairs are logged and no-op."""

    async def test_unknown_namespace_is_ignored(self) -> None:
        original = ApiBridgeConfig(max_lifecycle_events_per_query=4_321)
        sub, app_state = _make_subscriber(
            snapshot=original,
            resolver_int_return=99_999,
        )

        await sub.on_settings_changed("other", "max_lifecycle_events_per_query")

        assert app_state.api_bridge_config is original

    async def test_unknown_key_is_ignored(self) -> None:
        original = ApiBridgeConfig(max_lifecycle_events_per_query=4_321)
        sub, app_state = _make_subscriber(
            snapshot=original,
            resolver_int_return=99_999,
        )

        await sub.on_settings_changed("api", "some_unrelated_key")

        assert app_state.api_bridge_config is original
