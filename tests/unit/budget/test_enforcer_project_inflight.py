"""Unit tests for in-flight project budget checking via make_budget_checker."""

import pytest

from synthorg.budget.config import BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.tracker import CostTracker
from synthorg.core.enums import TaskType
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.providers.models import TokenUsage
from tests._shared import as_uuid

from .conftest import make_cost_record


def _make_task(budget_limit: float = 5.0) -> Task:
    return Task(
        id=as_uuid("task-001"),
        title="Test task",
        description="A test",
        type=TaskType.DEVELOPMENT,
        project="proj-1",
        created_by="manager",
        budget_limit=budget_limit,
    )


def _make_ctx(cost: float = 0.0) -> AgentContext:
    """Build an AgentContext with accumulated cost."""
    from datetime import date
    from uuid import uuid4

    from synthorg.core.agent import AgentIdentity, ModelConfig

    identity = AgentIdentity(
        id=uuid4(),
        name="Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        hiring_date=date(2026, 1, 1),
    )
    ctx = AgentContext.from_identity(identity)
    if cost > 0:
        ctx = ctx.model_copy(
            update={
                "accumulated_cost": TokenUsage(
                    input_tokens=0,
                    output_tokens=0,
                    cost=cost,
                ),
            },
        )
    return ctx


@pytest.mark.unit
class TestInFlightProjectBudget:
    """Tests for project budget checking in the in-flight checker."""

    async def test_checker_triggers_on_project_budget(self) -> None:
        """In-flight checker returns True when project budget exceeded."""
        cfg = BudgetConfig(total_monthly=1000.0)
        tracker = CostTracker(budget_config=cfg)
        # Pre-existing project cost
        await tracker.record(make_cost_record(project_id="proj-1", cost=8.0))
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)

        checker = await enforcer.make_budget_checker(
            _make_task(budget_limit=100.0),
            "agent-1",
            project_id="proj-1",
            project_budget=10.0,
        )
        assert checker is not None

        # Running cost of 3.0 -> 8.0 baseline + 3.0 = 11.0 >= 10.0
        ctx = _make_ctx(cost=3.0)
        assert checker(ctx) is True

    async def test_checker_passes_when_under_project_budget(self) -> None:
        """In-flight checker returns False when under project budget."""
        cfg = BudgetConfig(total_monthly=1000.0)
        tracker = CostTracker(budget_config=cfg)
        await tracker.record(make_cost_record(project_id="proj-1", cost=2.0))
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)

        checker = await enforcer.make_budget_checker(
            _make_task(budget_limit=100.0),
            "agent-1",
            project_id="proj-1",
            project_budget=10.0,
        )
        assert checker is not None

        # Running cost of 1.0 -> 2.0 baseline + 1.0 = 3.0 < 10.0
        ctx = _make_ctx(cost=1.0)
        assert checker(ctx) is False

    async def test_checker_no_project_budget_skips(self) -> None:
        """Zero project budget means no project check in the closure."""
        cfg = BudgetConfig(total_monthly=1000.0)
        tracker = CostTracker(budget_config=cfg)
        await tracker.record(make_cost_record(project_id="proj-1", cost=999.0))
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)

        checker = await enforcer.make_budget_checker(
            _make_task(budget_limit=100.0),
            "agent-1",
            project_id="proj-1",
            project_budget=0.0,
        )
        assert checker is not None

        # Even though project spent 999, project_budget=0 -> no check
        ctx = _make_ctx(cost=1.0)
        # Only task and monthly limits apply; with 100 task limit
        # and 1000 monthly, 1.0 running cost is fine
        assert checker(ctx) is False
