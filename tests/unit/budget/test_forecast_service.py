"""Unit tests for :class:`BudgetForecastService`.

The service owns the forecast read/write path the
``/budget/forecasts`` controller used to drive directly against the
repository. These cover the generate / get / approve / reject /
raise-ceiling flow against an in-memory repo double.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from synthorg.budget.config import BudgetConfig
from synthorg.budget.errors import RunHardCeilingTooLowError
from synthorg.budget.forecast_models import Forecast, ForecastDecision, HaltContext
from synthorg.budget.forecast_service import BudgetForecastService
from synthorg.budget.forecaster import CostForecaster
from synthorg.core.domain_errors import ConflictError, ResourceNotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence.cost_forecast_protocol import (
    CostForecastFilterSpec,
    CostForecastRepository,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


class _FakeForecastRepo:
    """In-memory cost forecast repo double."""

    def __init__(self, *forecasts: Forecast) -> None:
        self.rows: dict[str, Forecast] = {str(f.forecast_id): f for f in forecasts}
        self.saved: list[Forecast] = []

    async def get(self, entity_id: UUID, /) -> Forecast | None:
        return self.rows.get(str(entity_id))

    async def save(self, entity: Forecast, /) -> None:
        self.saved.append(entity)
        self.rows[str(entity.forecast_id)] = entity

    async def delete(self, entity_id: UUID, /) -> bool:
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
        /,
        entity_id: UUID,
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

    async def query(
        self,
        filter_spec: CostForecastFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Forecast, ...]:
        return tuple(list(self.rows.values())[offset : offset + limit])

    async def count(self, filter_spec: CostForecastFilterSpec) -> int:
        return len(self.rows)


def _pending_forecast(forecast_id: UUID | None = None) -> Forecast:
    return Forecast(
        forecast_id=forecast_id or uuid4(),
        brief_hash="a" * 64,
        estimated_cost=1.0,
        lower_bound=0.8,
        upper_bound=1.2,
        currency="USD",
        decision=ForecastDecision.PENDING,
        ceiling_amount=None,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _halted_forecast() -> Forecast:
    return _pending_forecast().model_copy(
        update={
            "decision": ForecastDecision.APPROVED,
            "decided_at": _NOW,
            "decided_by": "op-1",
            "ceiling_amount": 1.5,
            "halt_context": HaltContext(
                accumulated_cost=1.5,
                ceiling_amount=1.5,
                currency="USD",
                halted_at=_NOW,
            ),
        },
    )


def _service(repo: _FakeForecastRepo) -> BudgetForecastService:
    config = BudgetConfig(total_monthly=100.0)
    return BudgetForecastService(
        repo=repo,
        forecaster=CostForecaster(budget_config=config, clock=lambda: _NOW),
        budget_config=config,
        clock=lambda: _NOW,
    )


async def test_generate_persists_and_returns_forecast() -> None:
    repo = _FakeForecastRepo()
    service = _service(repo)
    forecast = await service.generate(
        brief_text=NotBlankStr("Build a thing"),
        role_skeleton=(NotBlankStr("role-1"),),
        model_assignments={},
        estimated_turns_per_role=None,
    )
    assert forecast.decision is ForecastDecision.PENDING
    assert repo.rows[str(forecast.forecast_id)] is forecast


async def test_get_or_404_returns_existing() -> None:
    forecast = _pending_forecast()
    service = _service(_FakeForecastRepo(forecast))
    assert await service.get_or_404(forecast.forecast_id) == forecast


async def test_get_or_404_raises_for_missing() -> None:
    service = _service(_FakeForecastRepo())
    with pytest.raises(ResourceNotFoundError):
        await service.get_or_404(uuid4())


async def test_approve_transitions_pending() -> None:
    forecast = _pending_forecast()
    repo = _FakeForecastRepo(forecast)
    service = _service(repo)
    approved = await service.approve(
        forecast.forecast_id,
        decided_by=NotBlankStr("op-1"),
        ceiling_amount=2.0,
    )
    assert approved.decision is ForecastDecision.APPROVED
    assert approved.ceiling_amount == 2.0


async def test_approve_missing_pending_raises_not_found() -> None:
    # An already-approved row is no longer pending, so the CAS fails.
    forecast = _halted_forecast()
    service = _service(_FakeForecastRepo(forecast))
    with pytest.raises(ResourceNotFoundError):
        await service.approve(
            forecast.forecast_id,
            decided_by=NotBlankStr("op-1"),
            ceiling_amount=None,
        )


async def test_reject_transitions_pending() -> None:
    forecast = _pending_forecast()
    service = _service(_FakeForecastRepo(forecast))
    rejected = await service.reject(
        forecast.forecast_id,
        decided_by=NotBlankStr("op-1"),
    )
    assert rejected.decision is ForecastDecision.REJECTED


async def test_raise_ceiling_below_accumulated_raises() -> None:
    forecast = _halted_forecast()
    repo = _FakeForecastRepo(forecast)
    service = _service(repo)
    with pytest.raises(RunHardCeilingTooLowError):
        await service.raise_ceiling(
            forecast.forecast_id,
            new_ceiling=1.4,
            accumulated_cost=1.5,
        )
    assert repo.saved == []


async def test_raise_ceiling_clears_halt() -> None:
    forecast = _halted_forecast()
    repo = _FakeForecastRepo(forecast)
    service = _service(repo)
    updated = await service.raise_ceiling(
        forecast.forecast_id,
        new_ceiling=3.0,
        accumulated_cost=1.5,
    )
    assert updated.ceiling_amount == 3.0
    assert updated.halt_context is None
    assert updated.updated_at == _NOW
    assert len(repo.saved) == 1


async def test_raise_ceiling_not_halted_raises_conflict() -> None:
    forecast = _pending_forecast()
    service = _service(_FakeForecastRepo(forecast))
    with pytest.raises(ConflictError):
        await service.raise_ceiling(
            forecast.forecast_id,
            new_ceiling=3.0,
            accumulated_cost=1.5,
        )


def test_repo_double_satisfies_protocol() -> None:
    # Guards the structural contract the service relies on at the boundary.
    assert isinstance(_FakeForecastRepo(), CostForecastRepository)
