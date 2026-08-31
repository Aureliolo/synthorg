"""``budget_enforcer_of`` narrows the slice's affordability protocol back to
the concrete ``BudgetEnforcer`` ``AgentEngine`` needs.

The slice field is typed ``BudgetAffordabilityChecker | None`` (a narrow
protocol) so the engine layer never imports the heavy concrete enforcer.
This accessor is the one place that narrowing happens for a caller whose
return value decides whether enforcement exists at all, so a wired-but-
wrong-type value must be visible rather than silently reading as unwired.
"""

import pytest
from structlog.testing import capture_logs

from synthorg.budget.affordability import BudgetAffordabilityChecker
from synthorg.budget.config import BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.state import BudgetStateSlice, budget_enforcer_of
from synthorg.budget.tracker import CostTracker
from synthorg.observability.events.budget import BUDGET_ENFORCER_WRONG_TYPE
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def test_returns_none_when_unwired() -> None:
    app_state = make_app_state()
    assert budget_enforcer_of(app_state) is None


def test_returns_the_concrete_enforcer_when_wired() -> None:
    config = BudgetConfig()
    tracker = CostTracker(budget_config=config)
    enforcer = BudgetEnforcer(budget_config=config, cost_tracker=tracker)
    app_state = make_app_state()
    app_state.wire(BudgetStateSlice, budget_enforcer=enforcer)

    assert budget_enforcer_of(app_state) is enforcer


def test_wrong_type_returns_none_and_logs() -> None:
    checker = mock_of[BudgetAffordabilityChecker]()
    app_state = make_app_state()
    app_state.wire(BudgetStateSlice, budget_enforcer=checker)

    with capture_logs() as logs:
        result = budget_enforcer_of(app_state)

    assert result is None
    assert any(
        entry["event"] == BUDGET_ENFORCER_WRONG_TYPE and entry["log_level"] == "warning"
        for entry in logs
    )
