"""Unit tests for ForecastBudgetController endpoint logic.

These call the controller methods directly with a fake ``State`` so the
handler logic (503 when unwired, the raise-ceiling guard, the happy
path) is covered without standing up a full TestClient.
"""

from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from litestar.datastructures import State

from synthorg.api.controllers.budget_forecast import (
    ForecastBudgetController,
    RaiseCeilingRequest,
)
from synthorg.budget.config import BudgetConfig
from synthorg.budget.errors import RunHardCeilingTooLowError
from synthorg.budget.forecast_models import Forecast, ForecastDecision, HaltContext
from synthorg.core.domain_errors import ServiceUnavailableError

pytestmark = pytest.mark.unit


def _controller() -> ForecastBudgetController:
    """Build a controller without Litestar's owner/route wiring.

    The handler methods only read their explicit ``state`` / ``data``
    arguments, so a route-free instance is sufficient to exercise their
    logic in isolation.
    """
    return object.__new__(ForecastBudgetController)


class _FakeForecastRepo:
    """In-memory cost forecast repo double for controller tests."""

    def __init__(self, forecast: Forecast | None = None) -> None:
        self.rows: dict[str, Forecast] = {}
        if forecast is not None:
            self.rows[str(forecast.forecast_id)] = forecast
        self.saved: list[Forecast] = []

    async def get(self, entity_id: object) -> Forecast | None:
        return self.rows.get(str(entity_id))

    async def save(self, entity: Forecast) -> None:
        self.saved.append(entity)
        self.rows[str(entity.forecast_id)] = entity


def _state(*, repo: object | None, budget_config: object | None) -> State:
    state = State()
    state.app_state = SimpleNamespace(
        cost_forecast_repo=repo,
        budget_config=budget_config,
        cost_forecaster=None,
    )
    return state


def _approved_forecast() -> Forecast:
    from datetime import UTC, datetime

    now = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    return Forecast(
        forecast_id=uuid4(),
        brief_hash="a" * 64,
        estimated_cost=1.0,
        lower_bound=0.8,
        upper_bound=1.2,
        currency="USD",
        decision=ForecastDecision.APPROVED,
        decided_at=now,
        decided_by="op-1",
        ceiling_amount=1.5,
        halt_context=HaltContext(
            accumulated_cost=1.5,
            ceiling_amount=1.5,
            currency="USD",
            halted_at=now,
        ),
        created_at=now,
        updated_at=now,
    )


async def test_create_forecast_503_when_unwired() -> None:
    """An unconfigured cost dial returns 503 rather than crashing."""
    controller = _controller()
    state = _state(repo=None, budget_config=None)
    with pytest.raises(ServiceUnavailableError):
        await ForecastBudgetController.create_forecast.fn(
            controller,
            data=cast("object", SimpleNamespace()),
            state=state,
        )


async def test_raise_ceiling_rejects_below_accumulated() -> None:
    """new_ceiling <= accumulated_cost -> typed RunHardCeilingTooLowError."""
    forecast = _approved_forecast()
    repo = _FakeForecastRepo(forecast)
    controller = _controller()
    state = _state(repo=repo, budget_config=BudgetConfig(total_monthly=100.0))

    with pytest.raises(RunHardCeilingTooLowError):
        await ForecastBudgetController.raise_ceiling.fn(
            controller,
            forecast_id=str(forecast.forecast_id),
            data=RaiseCeilingRequest(new_ceiling=1.4, accumulated_cost=1.5),
            state=state,
        )
    assert repo.saved == []


async def test_raise_ceiling_clears_halt_and_updates_ceiling() -> None:
    """A valid raise updates the ceiling and clears halt context."""
    forecast = _approved_forecast()
    repo = _FakeForecastRepo(forecast)
    controller = _controller()
    state = _state(repo=repo, budget_config=BudgetConfig(total_monthly=100.0))

    updated = await ForecastBudgetController.raise_ceiling.fn(
        controller,
        forecast_id=str(forecast.forecast_id),
        data=RaiseCeilingRequest(new_ceiling=3.0, accumulated_cost=1.5),
        state=state,
    )
    assert updated.ceiling_amount == 3.0
    assert updated.halt_context is None
    assert len(repo.saved) == 1
