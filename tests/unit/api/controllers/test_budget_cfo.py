"""Unit tests for BudgetCfoController endpoint logic.

Calls the handler methods directly with a fake ``State`` so the 503
(unwired) path and the happy path are covered without standing up a
full TestClient. The optimizer runs over a real ``CostTracker`` seeded
with cost records.
"""

import pytest
from litestar.datastructures import State

from synthorg.api.controllers.budget_cfo import BudgetCfoController
from synthorg.budget.config import BudgetConfig
from synthorg.budget.optimizer import CostOptimizer
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker import CostTracker
from synthorg.budget.trends import TrendPeriod
from synthorg.core.domain_errors import ServiceUnavailableError
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _controller() -> BudgetCfoController:
    """Build a route-free controller instance.

    The handler methods only read their explicit ``state`` argument, so
    a route-free instance suffices to exercise their logic.
    """
    return object.__new__(BudgetCfoController)


def _state(*, optimizer: CostOptimizer | None) -> State:
    state = State()
    state.app_state = make_app_state(
        slices={BudgetStateSlice: {"cost_optimizer": optimizer}},
    )
    return state


def _optimizer() -> CostOptimizer:
    bc = BudgetConfig(total_monthly=100.0)
    return CostOptimizer(cost_tracker=CostTracker(budget_config=bc), budget_config=bc)


async def test_anomalies_503_when_unwired() -> None:
    """An unconfigured cost optimizer returns 503 rather than crashing."""
    with pytest.raises(ServiceUnavailableError):
        await BudgetCfoController.detect_anomalies.fn(
            _controller(),
            state=_state(optimizer=None),
        )


async def test_efficiency_503_when_unwired() -> None:
    """An unconfigured cost optimizer returns 503 rather than crashing."""
    with pytest.raises(ServiceUnavailableError):
        await BudgetCfoController.analyze_efficiency.fn(
            _controller(),
            state=_state(optimizer=None),
        )


async def test_anomalies_happy_path_empty() -> None:
    """With no spend records the scan returns an empty anomaly set."""
    response = await BudgetCfoController.detect_anomalies.fn(
        _controller(),
        state=_state(optimizer=_optimizer()),
        period=TrendPeriod.SEVEN_DAYS,
        window_count=5,
    )
    assert response.data.anomalies == ()
    assert response.data.scan_period_start < response.data.scan_period_end


async def test_efficiency_happy_path_empty() -> None:
    """With no spend records the efficiency analysis lists no agents."""
    response = await BudgetCfoController.analyze_efficiency.fn(
        _controller(),
        state=_state(optimizer=_optimizer()),
        period=TrendPeriod.THIRTY_DAYS,
    )
    assert response.data.agents == ()
    assert response.data.analysis_period_start < response.data.analysis_period_end
