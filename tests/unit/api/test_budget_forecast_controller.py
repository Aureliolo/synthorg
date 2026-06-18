"""Unit tests for ForecastBudgetController endpoint logic.

These call the controller methods directly with a fake ``State`` so the
handler logic (503 when unwired, the raise-ceiling guard, the happy
path) is covered without standing up a full TestClient.
"""

from typing import cast
from uuid import uuid4

import pytest
from litestar.datastructures import State

from synthorg.api.controllers.budget_forecast import (
    ForecastBudgetController,
    ForecastRequest,
    RaiseCeilingRequest,
)
from synthorg.budget.config import BudgetConfig
from synthorg.budget.errors import RunHardCeilingTooLowError
from synthorg.budget.forecast_models import Forecast, ForecastDecision, HaltContext
from synthorg.budget.forecast_service import BudgetForecastService
from synthorg.budget.forecaster import CostForecaster
from synthorg.budget.state import BudgetStateSlice
from synthorg.core.domain_errors import ConflictError, ServiceUnavailableError
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence.cost_forecast_protocol import (
    CostForecastFilterSpec,
    CostForecastRepository,
)
from tests._shared import make_app_state

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

    async def delete(self, entity_id: object) -> bool:
        return self.rows.pop(str(entity_id), None) is not None

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Forecast, ...]:
        rows = list(self.rows.values())
        return tuple(rows[offset : offset + limit])

    async def transition_if(
        self,
        entity_id: object,
        from_state: ForecastDecision,
        to_state: ForecastDecision,
        **updates: object,
    ) -> bool:
        existing = self.rows.get(str(entity_id))
        if existing is None or existing.decision is not from_state:
            return False
        self.rows[str(entity_id)] = existing.model_copy(
            update={"decision": to_state, **updates},
        )
        return True

    async def raise_ceiling_if_halted(
        self,
        entity_id: object,
        *,
        new_ceiling: float,
        updated_at: object,
    ) -> bool:
        existing = self.rows.get(str(entity_id))
        if existing is None or existing.halt_context is None:
            return False
        cleared = existing.model_copy(
            update={
                "ceiling_amount": new_ceiling,
                "halt_context": None,
                "updated_at": updated_at,
            },
        )
        self.rows[str(entity_id)] = cleared
        self.saved.append(cleared)
        return True

    def _filter(self, spec: CostForecastFilterSpec) -> list[Forecast]:
        rows = list(self.rows.values())
        if spec.brief_hash is not None:
            rows = [f for f in rows if f.brief_hash == spec.brief_hash]
        if spec.decision is not None:
            rows = [f for f in rows if f.decision is spec.decision]
        return rows

    async def query(
        self,
        filter_spec: CostForecastFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Forecast, ...]:
        rows = self._filter(filter_spec)
        return tuple(rows[offset : offset + limit])

    async def count(self, filter_spec: CostForecastFilterSpec) -> int:
        return len(self._filter(filter_spec))


def _state(
    *,
    repo: object | None,
    budget_config: object | None,
    pareto_analyzer: object | None = None,
) -> State:
    state = State()
    forecast_service: BudgetForecastService | None = None
    if repo is not None and budget_config is not None:
        typed_config = cast("BudgetConfig", budget_config)
        forecast_service = BudgetForecastService(
            repo=cast("CostForecastRepository", repo),
            forecaster=CostForecaster(budget_config=typed_config),
            budget_config=typed_config,
        )
    state.app_state = make_app_state(
        cost_forecast_repo=repo,
        budget_config=budget_config,
        slices={
            BudgetStateSlice: {
                "pareto_analyzer": pareto_analyzer,
                "forecast_service": forecast_service,
            },
        },
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
            data=ForecastRequest(brief_text="brief", role_skeleton=("role-1",)),
            state=state,
        )


async def test_get_pareto_503_when_unwired() -> None:
    """An unconfigured Pareto analyzer returns 503, consistent with the
    rest of the cost-dial API, rather than a misleading empty frontier."""
    controller = _controller()
    state = _state(repo=None, budget_config=None, pareto_analyzer=None)
    with pytest.raises(ServiceUnavailableError):
        await ForecastBudgetController.get_pareto.fn(controller, state=state)


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


async def test_raise_ceiling_rejects_when_not_halted() -> None:
    """Raising the ceiling on a non-halted forecast is a 409 conflict."""
    forecast = _approved_forecast().model_copy(update={"halt_context": None})
    repo = _FakeForecastRepo(forecast)
    controller = _controller()
    state = _state(repo=repo, budget_config=BudgetConfig(total_monthly=100.0))

    with pytest.raises(ConflictError):
        await ForecastBudgetController.raise_ceiling.fn(
            controller,
            forecast_id=str(forecast.forecast_id),
            data=RaiseCeilingRequest(new_ceiling=3.0, accumulated_cost=1.5),
            state=state,
        )
    assert repo.saved == []
