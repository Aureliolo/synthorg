"""Tests for ``MemoryBridgeSettingsSubscriber``.

The subscriber re-resolves the whole ``MemoryBridgeConfig`` via
``ConfigResolver.get_memory_bridge_config`` on a watched
``memory.fine_tune_vram_batch_table`` change and swaps it wholesale (the
JSON VRAM table's parse + ordering validation lives in the resolver).
Tests cover protocol conformance, happy-path swap, unexpected
key/namespace no-op, resolver-failure (no swap, re-raised), and
``MemoryError`` propagation.
"""

from unittest.mock import AsyncMock, create_autospec

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.settings.bridge_configs import MemoryBridgeConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.memory_bridge_subscriber import (
    MemoryBridgeSettingsSubscriber,
)
from tests._shared import make_app_state

pytestmark = pytest.mark.unit

_WATCHED_KEY = "fine_tune_vram_batch_table"


def _make_subscriber(
    *,
    snapshot: MemoryBridgeConfig | None = None,
    resolved: MemoryBridgeConfig | None = None,
    side_effect: BaseException | None = None,
) -> tuple[MemoryBridgeSettingsSubscriber, AppState]:
    """Build a subscriber with a real AppState + spec'd ConfigResolver."""
    settings_service = create_autospec(SettingsService, instance=True)

    resolver = create_autospec(ConfigResolver, instance=True)
    if side_effect is not None:
        resolver.get_memory_bridge_config = AsyncMock(side_effect=side_effect)
    else:
        resolver.get_memory_bridge_config = AsyncMock(return_value=resolved)

    app_state = make_app_state(
        config=RootConfig(company_name="test"),
        approval_store=ApprovalStore(),
        config_resolver=resolver,
    )
    if snapshot is not None:
        app_state.bridge_config.swap_memory(snapshot)

    sub = MemoryBridgeSettingsSubscriber(
        app_state=app_state,
        settings_service=settings_service,
    )
    return sub, app_state


class TestSubscriberProtocol:
    """``MemoryBridgeSettingsSubscriber`` conforms to ``SettingsSubscriber``."""

    def test_isinstance_check(self) -> None:
        sub, _ = _make_subscriber(resolved=MemoryBridgeConfig())
        assert isinstance(sub, SettingsSubscriber)

    def test_watched_keys(self) -> None:
        sub, _ = _make_subscriber(resolved=MemoryBridgeConfig())
        assert sub.watched_keys == frozenset({("memory", _WATCHED_KEY)})

    def test_subscriber_name(self) -> None:
        sub, _ = _make_subscriber(resolved=MemoryBridgeConfig())
        assert sub.subscriber_name == "memory-bridge-config"


class TestRebuild:
    """``on_settings_changed`` re-resolves + swaps the whole snapshot."""

    async def test_change_swaps_full_resolved_snapshot(self) -> None:
        original = MemoryBridgeConfig()
        resolved = MemoryBridgeConfig(
            fine_tune_vram_batch_table=((48.0, 256), (24.0, 128)),
        )
        sub, app_state = _make_subscriber(snapshot=original, resolved=resolved)

        await sub.on_settings_changed("memory", _WATCHED_KEY)

        swapped = app_state.bridge_config.memory
        assert swapped is resolved
        assert swapped.fine_tune_vram_batch_table == ((48.0, 256), (24.0, 128))

    async def test_resolver_failure_does_not_swap(self) -> None:
        original = MemoryBridgeConfig(
            fine_tune_vram_batch_table=((40.0, 128),),
        )
        sub, app_state = _make_subscriber(
            snapshot=original,
            side_effect=RuntimeError("resolver outage"),
        )

        with pytest.raises(RuntimeError, match="resolver outage"):
            await sub.on_settings_changed("memory", _WATCHED_KEY)

        assert app_state.bridge_config.memory is original

    async def test_memory_error_propagates(self) -> None:
        sub, app_state = _make_subscriber(side_effect=MemoryError())
        before = app_state.bridge_config.memory

        with pytest.raises(MemoryError):
            await sub.on_settings_changed("memory", _WATCHED_KEY)

        assert app_state.bridge_config.memory is before


class TestUnexpectedRouting:
    """Unexpected (namespace, key) pairs are logged and no-op."""

    async def test_unknown_namespace_is_ignored(self) -> None:
        original = MemoryBridgeConfig()
        sub, app_state = _make_subscriber(
            snapshot=original,
            resolved=MemoryBridgeConfig(
                fine_tune_vram_batch_table=((48.0, 256),),
            ),
        )

        await sub.on_settings_changed("other", _WATCHED_KEY)

        assert app_state.bridge_config.memory is original

    async def test_unknown_key_is_ignored(self) -> None:
        original = MemoryBridgeConfig()
        sub, app_state = _make_subscriber(
            snapshot=original,
            resolved=MemoryBridgeConfig(
                fine_tune_vram_batch_table=((48.0, 256),),
            ),
        )

        await sub.on_settings_changed("memory", "some_unrelated_key")

        assert app_state.bridge_config.memory is original
