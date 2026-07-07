"""Tests for ResearchSettingsSubscriber."""

from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.research_subscriber import (
    ResearchSettingsSubscriber,
)
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _make_state(*, with_registry: bool = True) -> AppState:
    return make_app_state(
        config=RootConfig(company_name="test"),
        approval_store=ApprovalStore(),
        provider_registry=ProviderRegistry({}) if with_registry else None,
    )


def _make_subscriber(state: AppState) -> ResearchSettingsSubscriber:
    return ResearchSettingsSubscriber(
        app_state=state,
        settings_service=mock_of[SettingsService](),
    )


class TestProtocol:
    """Structural + metadata conformance."""

    def test_isinstance(self) -> None:
        assert isinstance(_make_subscriber(_make_state()), SettingsSubscriber)

    def test_subscriber_name(self) -> None:
        assert _make_subscriber(_make_state()).subscriber_name == "research-settings"

    def test_watches_tuning_keys_not_enabled(self) -> None:
        watched = _make_subscriber(_make_state()).watched_keys
        for key in (
            "model",
            "query_planner",
            "credibility_triage",
            "deduplicator",
            "synthesizer",
            "triage_batch_size",
            "hybrid_prefilter_factor",
            "dedup_similarity_threshold",
            "per_query_limit",
        ):
            assert ("research", key) in watched
        # A provider-registry rebuild re-bakes the strategies' retry handlers,
        # so the registry-swap key is watched too.
        assert ("providers", "retry_max_attempts") in watched
        # The master switch is the live per-request gate, never a rebuild.
        assert ("research", "enabled") not in watched


class TestRebuild:
    """on_settings_changed delegates to the research wiring factory."""

    async def test_change_rebuilds_via_factory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = _make_state(with_registry=True)
        registry = state.slice(ProvidersStateSlice).registry
        settings = mock_of[SettingsService]()
        build = AsyncMock()
        monkeypatch.setattr(
            "synthorg.api.lifecycle_helpers.feature_wiring._build_and_wire_research",
            build,
        )
        sub = ResearchSettingsSubscriber(app_state=state, settings_service=settings)
        await sub.on_settings_changed("research", "model")
        build.assert_awaited_once()
        call = build.await_args
        assert call is not None
        assert call.kwargs["provider_registry"] is registry
        # The rebuilt config must read DB overrides through the live settings
        # service, not a stale boot snapshot.
        assert call.kwargs["runtime_settings"] is settings

    async def test_no_registry_skips_rebuild(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = _make_state(with_registry=False)
        build = AsyncMock()
        monkeypatch.setattr(
            "synthorg.api.lifecycle_helpers.feature_wiring._build_and_wire_research",
            build,
        )
        sub = _make_subscriber(state)
        await sub.on_settings_changed("research", "synthesizer")
        build.assert_not_awaited()

    async def test_rebuild_failure_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = _make_state(with_registry=True)
        build = AsyncMock(side_effect=RuntimeError("db down"))
        monkeypatch.setattr(
            "synthorg.api.lifecycle_helpers.feature_wiring._build_and_wire_research",
            build,
        )
        sub = _make_subscriber(state)
        with pytest.raises(RuntimeError, match="db down"):
            await sub.on_settings_changed("research", "model")

    async def test_memory_error_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = _make_state(with_registry=True)
        build = AsyncMock(side_effect=MemoryError())
        monkeypatch.setattr(
            "synthorg.api.lifecycle_helpers.feature_wiring._build_and_wire_research",
            build,
        )
        sub = _make_subscriber(state)
        with pytest.raises(MemoryError):
            await sub.on_settings_changed("research", "model")
