"""Tests for charter wiring: live config resolution, and the approve path."""

from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.api.lifecycle_helpers.charter_wiring import (
    _charter_config_provider,
    _resolve_live_charter_config,
    attach_charter_dispatcher,
)
from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.budget.config import BudgetConfig
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.engine.state import EngineStateSlice
from synthorg.meta.charter.config import CharterConfig
from synthorg.meta.charter.service import CharterInterviewService
from synthorg.meta.charter.state import CharterStateSlice
from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.cost_forecast_protocol import CostForecastRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _resolver() -> ConfigResolver:
    async def _get_str(namespace: str, key: str) -> str:
        return {
            "interview_model": "example-medium-001",
            "default_currency": "EUR",
        }[key]

    async def _get_int(namespace: str, key: str) -> int:
        return {"interview_max_turns": 5, "interview_max_tokens": 1500}[key]

    async def _get_float(namespace: str, key: str) -> float:
        return 0.9

    return cast(
        "ConfigResolver",
        mock_of[ConfigResolver](
            get_str=AsyncMock(side_effect=_get_str),
            get_int=AsyncMock(side_effect=_get_int),
            get_float=AsyncMock(side_effect=_get_float),
        ),
    )


class TestResolveLiveCharterConfig:
    async def test_reads_all_five_keys_from_resolver(self) -> None:
        live = await _resolve_live_charter_config(_resolver(), fallback=CharterConfig())
        assert live.interview_model == "example-medium-001"
        assert live.default_currency == "EUR"
        assert live.interview_max_turns == 5
        assert live.interview_max_tokens == 1500
        assert live.interview_temperature == pytest.approx(0.9)

    async def test_strategy_discriminator_taken_from_fallback(self) -> None:
        live = await _resolve_live_charter_config(_resolver(), fallback=CharterConfig())
        assert live.interview_strategy == "llm"


class TestCharterConfigProvider:
    async def test_unwired_resolver_yields_boot_fallback(self) -> None:
        state = make_app_state()  # no config resolver wired
        fallback = CharterConfig(interview_max_turns=9)
        provide = _charter_config_provider(state, fallback=fallback)
        assert await provide() == fallback

    async def test_wired_resolver_yields_live_config(self) -> None:
        state = make_app_state(config_resolver=_resolver())
        provide = _charter_config_provider(state, fallback=CharterConfig())
        live = await provide()
        assert live.interview_model == "example-medium-001"
        assert live.interview_max_turns == 5


def _dispatch_state(backend: PersistenceBackend, **missing: bool) -> AppState:
    """Build an app state ready to attach the charter dispatcher.

    Returns:
        An ``AppState`` with every collaborator the attach needs, minus any
        named in *missing*.
    """
    kwargs: dict[str, object] = {
        "persistence": backend,
        "work_pipeline": mock_of[WorkPipeline](),
        "cost_forecast_repo": mock_of[CostForecastRepository](),
        "budget_config": BudgetConfig(),
    }
    for name in missing:
        del kwargs[name]
    return make_app_state(
        slices={
            CharterStateSlice: {"interview_service": mock_of[CharterInterviewService]()}
        },
        **kwargs,
    )


class TestAttachCharterDispatcher:
    """The approve path attaches on its own schedule, or names its condition.

    A live run met this as a 503 on the first charter it approved: the
    dispatcher was built during the interview service's own activation, three
    seconds before the work pipeline existed, and the activation's idempotency
    guard then returned early on every later reconciler pass, so the absence
    was permanent while ``GET /subsystems`` reported the charter engine active.
    """

    async def test_attaches_when_every_collaborator_is_present(self) -> None:
        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
        await backend.connect()
        state = _dispatch_state(backend)
        try:
            await attach_charter_dispatcher(state)
        finally:
            await backend.disconnect()

        assert state.slice(CharterStateSlice).dispatcher is not None

    async def test_a_later_pass_attaches_what_an_earlier_one_could_not(self) -> None:
        """The whole point: an absent collaborator is a wait, not a verdict."""
        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
        await backend.connect()
        state = _dispatch_state(backend, work_pipeline=True)
        try:
            with pytest.raises(SubsystemDeclinedError):
                await attach_charter_dispatcher(state)
            state.wire(EngineStateSlice, work_pipeline=mock_of[WorkPipeline]())
            await attach_charter_dispatcher(state)
        finally:
            await backend.disconnect()

        assert state.slice(CharterStateSlice).dispatcher is not None

    @pytest.mark.parametrize(
        ("absent", "condition"),
        [
            ("work_pipeline", "work pipeline"),
            ("cost_forecast_repo", "cost-forecast store"),
            ("budget_config", "budget config"),
        ],
    )
    async def test_names_the_collaborator_it_declined_on(
        self, absent: str, condition: str
    ) -> None:
        state = _dispatch_state(mock_of[PersistenceBackend](), **{absent: True})

        with pytest.raises(SubsystemDeclinedError, match=condition):
            await attach_charter_dispatcher(state)

    async def test_declines_without_the_interview_service_it_attaches_to(self) -> None:
        state = make_app_state(
            persistence=mock_of[PersistenceBackend](),
            work_pipeline=mock_of[WorkPipeline](),
            cost_forecast_repo=mock_of[CostForecastRepository](),
            budget_config=BudgetConfig(),
        )

        with pytest.raises(SubsystemDeclinedError, match="charter interview service"):
            await attach_charter_dispatcher(state)
