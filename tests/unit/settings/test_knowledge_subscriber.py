"""Tests for KnowledgeSettingsSubscriber."""

from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.observability.events.settings import SETTINGS_SERVICE_SWAP_FAILED
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.knowledge_subscriber import (
    KnowledgeSettingsSubscriber,
)
from tests._shared import make_app_state

pytestmark = pytest.mark.unit

_WIRE_TARGET = (
    "synthorg.api.lifecycle_helpers.knowledge_wiring._build_and_wire_knowledge"
)


def _make_state(*, with_registry: bool = True) -> AppState:
    return make_app_state(
        config=RootConfig(company_name="test"),
        approval_store=ApprovalStore(),
        provider_registry=ProviderRegistry({}) if with_registry else None,
    )


def _make_subscriber(state: AppState) -> KnowledgeSettingsSubscriber:
    return KnowledgeSettingsSubscriber(app_state=state)


class TestProtocol:
    """Structural + metadata conformance."""

    def test_isinstance(self) -> None:
        assert isinstance(_make_subscriber(_make_state()), SettingsSubscriber)

    def test_subscriber_name(self) -> None:
        assert _make_subscriber(_make_state()).subscriber_name == "knowledge-settings"

    def test_watches_synthesis_config_only(self) -> None:
        watched = _make_subscriber(_make_state()).watched_keys
        for key in (
            "synthesis_model",
            "synthesis_provider",
            "synthesis_synthesizer",
            "synthesis_max_chunks",
        ):
            assert ("knowledge", key) in watched
        # The enabled + /ask switches are live request gates, never a rebuild.
        assert ("knowledge", "enabled") not in watched
        assert ("knowledge", "synthesis_enabled") not in watched


class TestRebuild:
    """on_settings_changed delegates to the knowledge wiring factory."""

    async def test_change_rebuilds_via_factory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = _make_state(with_registry=True)
        registry = state.slice(ProvidersStateSlice).registry
        build = AsyncMock()
        monkeypatch.setattr(_WIRE_TARGET, build)
        sub = _make_subscriber(state)
        await sub.on_settings_changed("knowledge", "synthesis_model")
        build.assert_awaited_once()
        call = build.await_args
        assert call is not None
        assert call.kwargs["provider_registry"] is registry
        # A live synthesis-build failure must surface under the settings-swap
        # event, not the startup event, so an operator sees the breakage.
        assert call.kwargs["synthesis_failure_event"] == SETTINGS_SERVICE_SWAP_FAILED

    async def test_no_registry_skips_rebuild(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = _make_state(with_registry=False)
        build = AsyncMock()
        monkeypatch.setattr(_WIRE_TARGET, build)
        sub = _make_subscriber(state)
        await sub.on_settings_changed("knowledge", "synthesis_provider")
        build.assert_not_awaited()

    async def test_rebuild_failure_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = _make_state(with_registry=True)
        build = AsyncMock(side_effect=RuntimeError("db down"))
        monkeypatch.setattr(_WIRE_TARGET, build)
        sub = _make_subscriber(state)
        with pytest.raises(RuntimeError, match="db down"):
            await sub.on_settings_changed("knowledge", "synthesis_max_chunks")

    async def test_memory_error_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = _make_state(with_registry=True)
        build = AsyncMock(side_effect=MemoryError())
        monkeypatch.setattr(_WIRE_TARGET, build)
        sub = _make_subscriber(state)
        with pytest.raises(MemoryError):
            await sub.on_settings_changed("knowledge", "synthesis_model")
