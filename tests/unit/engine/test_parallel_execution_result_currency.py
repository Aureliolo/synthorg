"""Same-currency invariant tests for ``ParallelExecutionResult.total_cost``.

When agents in a parallel group run under different ``BudgetConfig``
currencies (e.g. one agent on a USD billing tier, another on EUR), the
group-level ``total_cost`` aggregation cannot produce a meaningful
sum.  The aggregator must raise
:class:`MixedCurrencyAggregationError` (HTTP 409) before any reduction
runs.
"""

from datetime import date

import pytest

from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.core.agent import AgentIdentity, ModelConfig, PersonalityConfig
from synthorg.core.enums import (
    Complexity,
    Priority,
    SeniorityLevel,
    TaskStatus,
    TaskType,
)
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.parallel_models import (
    AgentOutcome,
    ParallelExecutionResult,
)
from synthorg.engine.prompt import SystemPrompt
from synthorg.engine.run_result import AgentRunResult

pytestmark = pytest.mark.unit


def _make_identity(name: str) -> AgentIdentity:
    return AgentIdentity(
        name=name,
        role="engineer",
        department="engineering",
        level=SeniorityLevel.MID,
        hiring_date=date(2026, 1, 15),
        personality=PersonalityConfig(traits=("analytical",)),
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
    )


def _make_task(slug: str) -> Task:
    return Task(
        id=f"task-{slug}",
        title=slug,
        description="A test task",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="test-project",
        created_by="tester",
        assigned_to="test-agent",
        status=TaskStatus.ASSIGNED,
        estimated_complexity=Complexity.SIMPLE,
    )


def _make_run_result(
    identity: AgentIdentity,
    task: Task,
    *,
    currency: str,
) -> AgentRunResult:
    ctx = AgentContext.from_identity(identity, task=task)
    execution_result = ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.COMPLETED,
    )
    return AgentRunResult(
        execution_result=execution_result,
        system_prompt=SystemPrompt(
            content="test",
            template_version="1.0",
            estimated_tokens=1,
            sections=("identity",),
            metadata={"agent_id": str(identity.id)},
        ),
        duration_seconds=1.0,
        agent_id=str(identity.id),
        task_id=task.id,
        currency=currency,
    )


def _make_outcome(slug: str, *, currency: str) -> AgentOutcome:
    identity = _make_identity(slug)
    task = _make_task(slug)
    return AgentOutcome(
        task_id=task.id,
        agent_id=str(identity.id),
        result=_make_run_result(identity, task, currency=currency),
    )


class TestParallelExecutionResultTotalCostCurrency:
    """``ParallelExecutionResult.total_cost`` enforces same-currency outcomes."""

    def test_uniform_currency_sums(self) -> None:
        result = ParallelExecutionResult(
            group_id="group-1",
            outcomes=(
                _make_outcome("alpha", currency="USD"),
                _make_outcome("beta", currency="USD"),
            ),
            total_duration_seconds=1.0,
        )
        assert result.total_cost == pytest.approx(0.0)

    def test_mixed_currency_outcomes_raise(self) -> None:
        result = ParallelExecutionResult(
            group_id="group-1",
            outcomes=(
                _make_outcome("alpha", currency="USD"),
                _make_outcome("beta", currency="EUR"),
            ),
            total_duration_seconds=1.0,
        )
        with pytest.raises(MixedCurrencyAggregationError) as exc:
            _ = result.total_cost
        assert exc.value.currencies == frozenset({"USD", "EUR"})

    def test_failed_outcome_not_included_in_currency_check(self) -> None:
        """Failed outcomes (no result) carry no currency and must not
        contribute to the aggregation guard."""
        identity = _make_identity("failed")
        task = _make_task("failed")
        failed = AgentOutcome(
            task_id=task.id,
            agent_id=str(identity.id),
            error="boom",
        )
        result = ParallelExecutionResult(
            group_id="group-1",
            outcomes=(
                _make_outcome("alpha", currency="USD"),
                failed,
            ),
            total_duration_seconds=1.0,
        )
        # No raise: only the USD outcome is considered.
        assert result.total_cost == pytest.approx(0.0)

    def test_empty_outcomes_returns_zero(self) -> None:
        """Empty outcomes carry no currency; the sum is zero with no guard."""
        result = ParallelExecutionResult(
            group_id="group-1",
            outcomes=(),
            total_duration_seconds=0.0,
        )
        assert result.total_cost == 0.0
