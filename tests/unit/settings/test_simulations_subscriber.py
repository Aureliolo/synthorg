"""Tests for SimulationsSettingsSubscriber."""

from unittest.mock import AsyncMock

import pytest

from synthorg.api.state import AppState
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.client.state import ClientStateSlice
from synthorg.config.schema import RootConfig
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.review.pipeline import ReviewPipeline
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.simulations_subscriber import (
    SimulationsSettingsSubscriber,
)
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit

_RELOAD_TARGET = "synthorg.workers.runtime_builder.reload_runtime_services"


def _make_state(*, with_runtime: bool = True) -> AppState:
    config = RootConfig(company_name="test")
    if not with_runtime:
        return make_app_state(config=config)
    sim = ClientSimulationState(
        intake_engine=mock_of[IntakeEngine](),
        review_pipeline=mock_of[ReviewPipeline](),
        intake_default_project="client-intake",
    )
    return make_app_state(
        config=config,
        slices={ClientStateSlice: {"simulation_state": sim}},
    )


def _make_subscriber(state: AppState) -> SimulationsSettingsSubscriber:
    return SimulationsSettingsSubscriber(app_state=state)


class TestProtocol:
    """Structural + metadata conformance."""

    def test_isinstance(self) -> None:
        assert isinstance(_make_subscriber(_make_state()), SettingsSubscriber)

    def test_subscriber_name(self) -> None:
        assert _make_subscriber(_make_state()).subscriber_name == "simulations-settings"

    def test_watches_intake_and_review_keys(self) -> None:
        watched = _make_subscriber(_make_state()).watched_keys
        for key in (
            "intake_strategy",
            "intake_model",
            "intake_default_project",
            "review_pipeline_strategy",
            "verification_review_enabled",
            "verification_grader",
            "verification_decomposer",
        ):
            assert ("simulations", key) in watched


class TestRebuild:
    """on_settings_changed routes through the full runtime reload."""

    async def test_change_triggers_runtime_reload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = _make_state(with_runtime=True)
        reload = AsyncMock()
        monkeypatch.setattr(_RELOAD_TARGET, reload)
        sub = _make_subscriber(state)
        await sub.on_settings_changed("simulations", "intake_strategy")
        reload.assert_awaited_once_with(state)

    async def test_no_runtime_skips_reload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = _make_state(with_runtime=False)
        reload = AsyncMock()
        monkeypatch.setattr(_RELOAD_TARGET, reload)
        sub = _make_subscriber(state)
        await sub.on_settings_changed("simulations", "intake_strategy")
        reload.assert_not_awaited()

    async def test_reload_failure_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = _make_state(with_runtime=True)
        reload = AsyncMock(side_effect=RuntimeError("reload boom"))
        monkeypatch.setattr(_RELOAD_TARGET, reload)
        sub = _make_subscriber(state)
        with pytest.raises(RuntimeError, match="reload boom"):
            await sub.on_settings_changed("simulations", "review_pipeline_strategy")

    async def test_memory_error_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = _make_state(with_runtime=True)
        reload = AsyncMock(side_effect=MemoryError())
        monkeypatch.setattr(_RELOAD_TARGET, reload)
        sub = _make_subscriber(state)
        with pytest.raises(MemoryError):
            await sub.on_settings_changed("simulations", "intake_model")
