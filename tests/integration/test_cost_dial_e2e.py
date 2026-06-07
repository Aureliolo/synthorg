"""End-to-end harness for the cost dial.

Validates the full operator-facing flow with in-memory doubles:

1. Submit a brief via the forecast gate.
2. A pre-flight ``Forecast`` row is created with ``decision=pending``;
   the work pipeline refuses to dispatch.
3. Operator approves via ``transition_if`` (the controller's
   approve path under the hood).
4. Re-running the gate dispatches into the pipeline.
5. The per-turn ``BudgetChecker`` enforces the hard ceiling and
   raises ``RunHardCeilingExceededError``.
6. The engine's ``_handle_budget_error`` routes ceiling crossings to
   ``TerminationReason.PARKED`` when an ``ApprovalGate`` is wired.
7. Operator raises the ceiling; the next turn dispatches cleanly.
8. ``ParetoAnalyzer`` returns a frontier with the run's roles.

The test exercises every cost-dial component (forecaster, gate,
repo, hard-ceiling checker, agent-engine routing, Pareto analyzer)
in one cohesive path so a regression in any layer surfaces here.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from synthorg.budget.benchmark_stub import StubBenchmarkScoreProvider
from synthorg.budget.config import AutoDowngradeConfig, BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.errors import (
    CostForecastApprovalRequiredError,
    RunHardCeilingExceededError,
)
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.budget.forecaster import CostForecaster
from synthorg.budget.pareto import (
    ParetoAnalyzer,
    ParetoFrontier,
    RoleAssignment,
)
from synthorg.budget.tracker import CostTracker
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import Priority, TaskStatus, TaskType
from synthorg.core.task import Task
from synthorg.engine.agent_engine_errors import AgentEngineErrorsMixin
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.pipeline.forecast_gate import ForecastGate
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    RoutingVerdict,
    WorkItem,
    WorkPhaseResult,
    WorkPipelineResult,
    WorkSource,
)
from synthorg.engine.pipeline.narrator_port import RunNarrator
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.integration


# ── Test doubles ──────────────────────────────────────────────────


class _InMemoryForecastRepo:
    """In-memory ``CostForecastRepository`` for the e2e harness."""

    def __init__(self) -> None:
        self.rows: dict[UUID, Forecast] = {}

    async def save(self, entity: Forecast) -> None:
        self.rows[entity.forecast_id] = entity

    async def get(self, entity_id: UUID) -> Forecast | None:
        return self.rows.get(entity_id)

    async def delete(self, entity_id: UUID) -> bool:
        return self.rows.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[Forecast, ...]:
        ordered = sorted(self.rows.values(), key=lambda f: f.created_at, reverse=True)
        return tuple(ordered[offset : offset + limit])

    async def transition_if(
        self,
        entity_id: UUID,
        from_state: ForecastDecision,
        to_state: ForecastDecision,
        **updates: object,
    ) -> bool:
        row = self.rows.get(entity_id)
        if row is None or row.decision is not from_state:
            return False
        merged: dict[str, object] = {"decision": to_state}
        if "decided_by" in updates:
            merged["decided_by"] = updates["decided_by"]
        if "ceiling_amount" in updates:
            merged["ceiling_amount"] = updates["ceiling_amount"]
        if to_state is not ForecastDecision.PENDING:
            merged["decided_at"] = datetime.now(UTC)
        self.rows[entity_id] = row.model_copy(update=merged)
        return True

    async def query(
        self, _filter: object, *, limit: int = 100, offset: int = 0
    ) -> tuple[Forecast, ...]:
        return await self.list_items(limit=limit, offset=offset)

    async def count(self, _filter: object) -> int:
        return len(self.rows)


class _StubWorkPipeline:
    """Bare-bones pipeline that returns a synthetic success."""

    def __init__(self) -> None:
        self.calls: list[WorkItem] = []
        self.narrator: RunNarrator | None = None

    async def run(self, work_item: WorkItem) -> WorkPipelineResult:
        self.calls.append(work_item)
        return WorkPipelineResult(
            work_item=work_item,
            verdict=RoutingVerdict.LEAF,
            execution_path=ExecutionPath.SOLO,
            task_id="task-001",
            final_task_status=TaskStatus.COMPLETED,
            phases=(
                WorkPhaseResult(phase="intake", success=True, duration_seconds=0.01),
            ),
            total_duration_seconds=0.01,
        )

    def attach_narrator(self, narrator: RunNarrator) -> None:
        self.narrator = narrator


class _EngineHost(AgentEngineErrorsMixin):
    """Host for the engine error mixin under test."""

    def __init__(self, *, approval_gate: object | None) -> None:
        self._approval_gate = approval_gate
        self._cost_tracker = None


# ── Fixtures ──────────────────────────────────────────────────────


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="Backend Engineer",
        role="Engineer",
        department="Engineering",
        model=ModelConfig(
            provider="example-provider",
            model_id="example-medium-001",
        ),
        hiring_date=date(2026, 1, 1),
    )


def _task(
    *,
    hard_ceiling: float | None = None,
    forecast_id: UUID | None = None,
) -> Task:
    return Task(
        id=as_uuid("task-e2e"),
        title="Plan the marketing site",
        description="Rebuild the landing experience.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="marketing",
        created_by="op-1",
        hard_ceiling=hard_ceiling,
        forecast_id=forecast_id,
    )


def _work_item(*, forecast_id: UUID | None = None) -> WorkItem:
    return WorkItem(
        origin_adapter_id="intake-entry-adapter",
        source=WorkSource.INTAKE,
        title="Marketing site rebuild",
        raw_intent="Build a landing experience that converts.",
        project="marketing",
        requested_by="operator-1",
        forecast_id=forecast_id,
    )


def _budget_config() -> BudgetConfig:
    return BudgetConfig(
        total_monthly=100.0,
        run_hard_ceiling=0.0,
        forecast_required=True,
        auto_downgrade=AutoDowngradeConfig(
            enabled=False,
            downgrade_map=(("large", "medium"), ("medium", "small")),
        ),
    )


def _checker_ctx(*, accumulated_cost: float) -> Any:
    """Stub agent context with the single attribute the closure reads."""
    return SimpleNamespace(
        accumulated_cost=SimpleNamespace(cost=accumulated_cost),
    )


# ── Test ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cost_dial_full_lifecycle() -> None:
    """End-to-end: forecast gate -> approve -> dispatch -> ceiling -> resume."""
    budget = _budget_config()
    repo = _InMemoryForecastRepo()
    pipeline = _StubWorkPipeline()

    async def _no_history(_tier: str, _role: str) -> Sequence[float]:
        return ()

    forecaster = CostForecaster(
        budget_config=budget,
        history_lookup=cast("Callable[..., Awaitable[Sequence[float]]]", _no_history),
    )
    gate = ForecastGate(
        work_pipeline=pipeline,
        forecaster=forecaster,
        forecast_repo=cast("Any", repo),
        budget_config=budget,
    )

    # 1. First submission: forecast row created (pending), gate raises.
    with pytest.raises(CostForecastApprovalRequiredError) as info:
        await gate.run(_work_item())

    forecast_id = info.value.forecast_id
    assert info.value.estimated_cost > 0
    assert forecast_id in repo.rows
    assert repo.rows[forecast_id].decision is ForecastDecision.PENDING
    assert pipeline.calls == []

    # 2. Operator approves with a per-run ceiling.
    approved = await repo.transition_if(
        forecast_id,
        ForecastDecision.PENDING,
        ForecastDecision.APPROVED,
        decided_by="op-1",
        ceiling_amount=1.50,
    )
    assert approved is True

    # 3. Re-running the gate with the approved forecast id dispatches.
    result = await gate.run(_work_item(forecast_id=forecast_id))
    assert result.task_id == "task-001"
    assert len(pipeline.calls) == 1

    # 4. The per-turn checker honors the per-task hard ceiling and
    #    raises RunHardCeilingExceededError when the accumulated cost
    #    crosses the line.
    enforcer = BudgetEnforcer(
        budget_config=budget,
        cost_tracker=CostTracker(),
    )
    task_with_ceiling = _task(hard_ceiling=1.50, forecast_id=forecast_id)
    checker = await enforcer.make_budget_checker(task_with_ceiling, "agent-1")
    assert checker is not None
    with pytest.raises(RunHardCeilingExceededError) as ceiling_info:
        checker(_checker_ctx(accumulated_cost=1.50))
    assert ceiling_info.value.ceiling_amount == pytest.approx(1.50)
    assert ceiling_info.value.task_id == sid("task-e2e")
    assert ceiling_info.value.forecast_id == forecast_id

    # 5. With an ApprovalGate wired, the engine's error handler routes
    #    the ceiling crossing to TerminationReason.PARKED so the
    #    operator can raise the ceiling and resume.
    async def _park_ok(**_: object) -> None:
        return None

    engine = _EngineHost(approval_gate=SimpleNamespace(park_context=_park_ok))
    run_result = cast(
        "Any",
        await engine._handle_budget_error(
            exc=ceiling_info.value,
            identity=_identity(),
            task=task_with_ceiling,
            agent_id="agent-1",
            task_id=sid("task-e2e"),
            duration_seconds=0.1,
        ),
    )
    assert run_result.execution_result.termination_reason is TerminationReason.PARKED

    # 6. Operator raises the ceiling; the checker stops raising for
    #    cost values up to the new line.
    raised_ceiling_task = _task(hard_ceiling=3.00, forecast_id=forecast_id)
    resumed_checker = await enforcer.make_budget_checker(
        raised_ceiling_task,
        "agent-1",
    )
    assert resumed_checker is not None
    assert resumed_checker(_checker_ctx(accumulated_cost=1.50)) is False

    # 7. Pareto analyzer returns a frontier referencing the role(s)
    #    that ran. The stub provider supplies calibrated quality
    #    scores; the frontier surfaces its provenance via ``source``.
    async def _assignments() -> Sequence[RoleAssignment]:
        return (
            RoleAssignment(
                role_id="engineer",
                role_label="Backend Engineer",
                current_model="example-large-001",
                current_cost_per_task=1.20,
            ),
        )

    analyzer = ParetoAnalyzer(
        benchmark_provider=StubBenchmarkScoreProvider(),
        budget_config=budget,
        assignment_lookup=_assignments,
    )
    frontier = await analyzer.analyse()
    assert isinstance(frontier, ParetoFrontier)
    assert len(frontier.points) == 1
    point = frontier.points[0]
    assert point.role_label == "Backend Engineer"
    assert point.candidate_model == "example-medium-001"
    assert "stub:calibrated-v1" in frontier.source
    _: Mapping[str, object] = {}  # type-check pin for the Mapping import
