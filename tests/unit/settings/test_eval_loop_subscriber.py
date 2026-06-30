"""Tests for EvalLoopSettingsSubscriber + the pattern-strategy reload helper."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.hr.evaluation.loop_coordinator import EvalLoopCoordinator
from synthorg.hr.state import HrStateSlice
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.eval_loop_subscriber import (
    EvalLoopSettingsSubscriber,
)
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit

_RELOAD_TARGET = (
    "synthorg.api.lifecycle_helpers.eval_loop_wiring."
    "reload_eval_loop_pattern_strategies"
)


def _make_state(*, with_registry: bool = True) -> AppState:
    return make_app_state(
        config=RootConfig(company_name="test"),
        approval_store=ApprovalStore(),
        provider_registry=ProviderRegistry({}) if with_registry else None,
    )


def _make_subscriber(state: AppState) -> EvalLoopSettingsSubscriber:
    return EvalLoopSettingsSubscriber(
        app_state=state,
        settings_service=mock_of[SettingsService](),
    )


class TestProtocol:
    def test_isinstance(self) -> None:
        assert isinstance(_make_subscriber(_make_state()), SettingsSubscriber)

    def test_subscriber_name(self) -> None:
        assert _make_subscriber(_make_state()).subscriber_name == "eval-loop-settings"

    def test_watches_model_mode_keys_only(self) -> None:
        watched = _make_subscriber(_make_state()).watched_keys
        for key in (
            "eval_loop_llm_model",
            "eval_loop_llm_provider",
            "eval_loop_pattern_identifier_mode",
            "eval_loop_fix_proposer_mode",
        ):
            assert ("hr", key) in watched
        # The cycle enable / cadence knobs are re-read live per tick, not here.
        assert ("hr", "eval_loop_cycle_enabled") not in watched
        assert ("hr", "eval_loop_cycle_interval_seconds") not in watched


class TestRebuild:
    async def test_change_delegates_to_reload_with_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = _make_state(with_registry=True)
        registry = state.slice(ProvidersStateSlice).registry
        reload = AsyncMock()
        monkeypatch.setattr(_RELOAD_TARGET, reload)
        sub = _make_subscriber(state)

        await sub.on_settings_changed("hr", "eval_loop_llm_model")

        reload.assert_awaited_once_with(state, provider_registry=registry)

    async def test_reload_failure_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = _make_state(with_registry=True)
        reload = AsyncMock(side_effect=RuntimeError("db down"))
        monkeypatch.setattr(_RELOAD_TARGET, reload)
        sub = _make_subscriber(state)

        with pytest.raises(RuntimeError, match="db down"):
            await sub.on_settings_changed("hr", "eval_loop_fix_proposer_mode")


class TestReloadHelper:
    """``reload_eval_loop_pattern_strategies`` resolves + swaps strategies."""

    async def test_swaps_deterministic_strategies_onto_coordinator(self) -> None:
        from synthorg.api.lifecycle_helpers.eval_loop_wiring import (
            reload_eval_loop_pattern_strategies,
        )

        coordinator = mock_of[EvalLoopCoordinator](set_pattern_strategies=MagicMock())
        resolver = mock_of[ConfigResolver](
            get_str=AsyncMock(return_value="deterministic"),
        )
        state = make_app_state(
            config_resolver=resolver,
            slices={HrStateSlice: {"eval_loop_coordinator": coordinator}},
        )

        await reload_eval_loop_pattern_strategies(state)

        # Deterministic modes resolve to no provider-backed overrides.
        coordinator.set_pattern_strategies.assert_called_once_with(
            pattern_identifier=None, fix_proposer=None
        )

    async def test_no_coordinator_is_noop(self) -> None:
        from synthorg.api.lifecycle_helpers.eval_loop_wiring import (
            reload_eval_loop_pattern_strategies,
        )

        state = make_app_state()
        # No coordinator wired: the helper returns without resolving anything.
        await reload_eval_loop_pattern_strategies(state)
