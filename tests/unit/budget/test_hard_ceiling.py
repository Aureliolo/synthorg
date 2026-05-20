"""Hard real-money ceiling enforcement.

Covers the per-brief absolute ceiling honored by
``BudgetEnforcer.make_budget_checker``. Two failure modes:

* per-task ``Task.hard_ceiling`` set: the closure raises
  ``RunHardCeilingExceededError`` the moment accumulated cost crosses it.
* per-task absent: the closure falls back to the global setting
  ``budget.run_hard_ceiling`` (zero meaning disabled).

``RunHardCeilingExceededError`` is a subclass of
``BudgetExhaustedError`` so existing ``AgentEngine`` catch handlers
absorb it without changes; the engine then routes to the park / resume
path covered separately in
``tests/unit/engine/test_agent_engine_ceiling_park.py``.
"""

from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from synthorg.budget.config import BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.errors import (
    BudgetExhaustedError,
    RunHardCeilingExceededError,
)
from synthorg.budget.tracker import CostTracker
from synthorg.core.enums import TaskType
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext

pytestmark = pytest.mark.unit


def _config(
    *,
    run_hard_ceiling: float = 0.0,
    total_monthly: float = 100.0,
) -> BudgetConfig:
    return BudgetConfig(
        total_monthly=total_monthly,
        run_hard_ceiling=run_hard_ceiling,
    )


def _task(
    *,
    hard_ceiling: float | None = None,
    budget_limit: float = 0.0,
) -> Task:
    return Task(
        id="task-1",
        title="Plan a thing",
        description="Plan the thing carefully.",
        type=TaskType.DEVELOPMENT,
        project="proj-1",
        created_by="op-1",
        budget_limit=budget_limit,
        hard_ceiling=hard_ceiling,
    )


def _context(*, accumulated_cost: float) -> AgentContext:
    """Minimal AgentContext-shaped stub for the checker closure.

    The closure only reads ``ctx.accumulated_cost.cost``, so a
    :class:`SimpleNamespace` with that attribute matches what the
    closure structurally needs without paying the full
    AgentContext construction cost.
    """
    cost_ns = SimpleNamespace(cost=accumulated_cost)
    return cast("AgentContext", SimpleNamespace(accumulated_cost=cost_ns))


@pytest.mark.asyncio
async def test_per_task_hard_ceiling_triggers_raise_at_threshold() -> None:
    """Task.hard_ceiling=1.50 with accumulated 1.50 raises immediately."""
    tracker = CostTracker()
    enforcer = BudgetEnforcer(
        budget_config=_config(),
        cost_tracker=tracker,
    )
    checker = await enforcer.make_budget_checker(_task(hard_ceiling=1.50), "agent-1")
    assert checker is not None

    with pytest.raises(RunHardCeilingExceededError) as info:
        checker(_context(accumulated_cost=1.50))

    assert info.value.ceiling_amount == pytest.approx(1.50)
    assert info.value.accumulated_cost == pytest.approx(1.50)
    assert info.value.currency == "USD"
    assert info.value.task_id == "task-1"


@pytest.mark.asyncio
async def test_closure_propagates_task_forecast_id() -> None:
    """The raised error carries ``task.forecast_id`` for halt stamping.

    This is the seam the engine reads to stamp the forecast row so the
    dashboard can surface the resume banner; if the closure dropped it,
    the banner would never render in production.
    """
    forecast_id = uuid4()
    tracker = CostTracker()
    enforcer = BudgetEnforcer(budget_config=_config(), cost_tracker=tracker)
    task = _task(hard_ceiling=1.50).model_copy(update={"forecast_id": forecast_id})
    checker = await enforcer.make_budget_checker(task, "agent-1")
    assert checker is not None

    with pytest.raises(RunHardCeilingExceededError) as info:
        checker(_context(accumulated_cost=1.60))

    assert info.value.forecast_id == forecast_id


@pytest.mark.asyncio
async def test_global_run_hard_ceiling_used_when_task_field_absent() -> None:
    """Task.hard_ceiling=None falls back to budget.run_hard_ceiling."""
    tracker = CostTracker()
    enforcer = BudgetEnforcer(
        budget_config=_config(run_hard_ceiling=2.00),
        cost_tracker=tracker,
    )
    checker = await enforcer.make_budget_checker(_task(), "agent-1")
    assert checker is not None

    with pytest.raises(RunHardCeilingExceededError) as info:
        checker(_context(accumulated_cost=2.00))

    assert info.value.ceiling_amount == pytest.approx(2.00)


@pytest.mark.asyncio
async def test_below_ceiling_does_not_raise() -> None:
    """Below the ceiling the closure returns the normal bool result."""
    tracker = CostTracker()
    enforcer = BudgetEnforcer(
        budget_config=_config(run_hard_ceiling=10.0),
        cost_tracker=tracker,
    )
    checker = await enforcer.make_budget_checker(_task(), "agent-1")
    assert checker is not None

    # Accumulated < ceiling -> the closure returns False (not exhausted)
    # and does not raise.
    assert checker(_context(accumulated_cost=1.0)) is False


@pytest.mark.asyncio
async def test_zero_ceiling_means_disabled_no_raise() -> None:
    """Both Task.hard_ceiling=None and run_hard_ceiling=0 disable enforcement."""
    tracker = CostTracker()
    enforcer = BudgetEnforcer(
        budget_config=_config(run_hard_ceiling=0.0, total_monthly=50.0),
        cost_tracker=tracker,
    )
    checker = await enforcer.make_budget_checker(_task(), "agent-1")
    assert checker is not None

    # With hard-ceiling disabled (run_hard_ceiling=0, Task.hard_ceiling
    # None) and accumulated cost below the monthly budget, the checker
    # returns False: no limit is hit. Using a value under total_monthly
    # keeps monthly exhaustion from masking the hard-ceiling disablement.
    assert checker(_context(accumulated_cost=10.0)) is False


@pytest.mark.asyncio
async def test_run_hard_ceiling_error_is_budget_exhausted_subclass() -> None:
    """RunHardCeilingExceededError inherits BudgetExhaustedError.

    The engine's existing ``except BudgetExhaustedError`` catch must
    absorb the ceiling error without code changes.
    """
    err = RunHardCeilingExceededError(
        "test",
        ceiling_amount=1.0,
        accumulated_cost=1.0,
        currency="USD",
    )
    assert isinstance(err, BudgetExhaustedError)
