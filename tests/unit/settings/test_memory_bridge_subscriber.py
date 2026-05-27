"""Tests for ``MemoryBridgeSettingsSubscriber``.

The subscriber re-resolves the whole ``MemoryBridgeConfig`` via
``ConfigResolver.get_memory_bridge_config`` on any watched
``memory.*`` bridge-key change and swaps it wholesale (the JSON VRAM
table's parse + ordering validation lives in the resolver). Tests
cover protocol conformance, happy-path swap, unexpected key/namespace
no-op, resolver-failure (no swap, re-raised), and ``MemoryError``
propagation.
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
        app_state.swap_memory_bridge_config(snapshot)

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
        assert sub.watched_keys == frozenset(
            {
                ("memory", "consolidation_enforce_batch_size"),
                ("memory", "fine_tune_vram_batch_table"),
                ("memory", "fine_tune_chunk_size"),
            }
        )

    def test_subscriber_name(self) -> None:
        sub, _ = _make_subscriber(resolved=MemoryBridgeConfig())
        assert sub.subscriber_name == "memory-bridge-config"


class TestRebuild:
    """``on_settings_changed`` re-resolves + swaps the whole snapshot."""

    async def test_change_swaps_full_resolved_snapshot(self) -> None:
        original = MemoryBridgeConfig(fine_tune_chunk_size=512)
        resolved = MemoryBridgeConfig(
            fine_tune_chunk_size=2048,
            consolidation_enforce_batch_size=5000,
        )
        sub, app_state = _make_subscriber(snapshot=original, resolved=resolved)

        await sub.on_settings_changed("memory", "fine_tune_chunk_size")

        swapped = app_state.memory_bridge_config
        assert swapped is resolved
        assert swapped.fine_tune_chunk_size == 2048
        assert swapped.consolidation_enforce_batch_size == 5000

    async def test_resolver_failure_does_not_swap(self) -> None:
        original = MemoryBridgeConfig(fine_tune_chunk_size=768)
        sub, app_state = _make_subscriber(
            snapshot=original,
            side_effect=RuntimeError("resolver outage"),
        )

        with pytest.raises(RuntimeError, match="resolver outage"):
            await sub.on_settings_changed("memory", "fine_tune_chunk_size")

        assert app_state.memory_bridge_config is original
        assert app_state.memory_bridge_config.fine_tune_chunk_size == 768

    async def test_memory_error_propagates(self) -> None:
        sub, app_state = _make_subscriber(side_effect=MemoryError())
        before = app_state.memory_bridge_config

        with pytest.raises(MemoryError):
            await sub.on_settings_changed("memory", "fine_tune_chunk_size")

        assert app_state.memory_bridge_config is before


class TestUnexpectedRouting:
    """Unexpected (namespace, key) pairs are logged and no-op."""

    async def test_unknown_namespace_is_ignored(self) -> None:
        original = MemoryBridgeConfig(fine_tune_chunk_size=640)
        sub, app_state = _make_subscriber(
            snapshot=original,
            resolved=MemoryBridgeConfig(fine_tune_chunk_size=4096),
        )

        await sub.on_settings_changed("other", "fine_tune_chunk_size")

        assert app_state.memory_bridge_config is original

    async def test_unknown_key_is_ignored(self) -> None:
        original = MemoryBridgeConfig(fine_tune_chunk_size=640)
        sub, app_state = _make_subscriber(
            snapshot=original,
            resolved=MemoryBridgeConfig(fine_tune_chunk_size=4096),
        )

        await sub.on_settings_changed("memory", "some_unrelated_key")

        assert app_state.memory_bridge_config is original
