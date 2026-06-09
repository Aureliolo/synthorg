"""AgentEngine routes RunHardCeilingExceededError to PARKED.

When the per-turn ``BudgetChecker`` raises
``RunHardCeilingExceededError`` mid-run, the engine catches it via the
existing ``except BudgetExhaustedError`` handler and routes to
``TerminationReason.PARKED`` (instead of the default
``BUDGET_EXHAUSTED``) when an ``ApprovalGate`` is wired. This unit
test exercises the routing directly against ``_handle_budget_error``.

On a successful park the engine also stamps the halt context onto the
forecast row (via ``CostForecastRepository``) so the dashboard can
surface a "run halted: ceiling exceeded" banner with the accumulated
cost and the ceiling that was crossed. The stamp is best-effort: a
missing repo or a missing forecast id degrades silently.
"""

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import pytest

from synthorg.approval.models import EscalationInfo
from synthorg.budget.errors import (
    BudgetExhaustedError,
    RunHardCeilingExceededError,
)
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskType
from synthorg.engine.agent_engine_errors import AgentEngineErrorsMixin
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import TerminationReason
from tests._shared import as_uuid, sid

if TYPE_CHECKING:
    from synthorg.persistence.cost_forecast_protocol import CostForecastRepository

pytestmark = pytest.mark.unit


class _FakeApprovalGate:
    """Async ApprovalGate double recording park_context calls."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail = fail

    async def park_context(
        self,
        *,
        escalation: EscalationInfo,
        context: AgentContext,
        agent_id: str,
        task_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        self.calls.append(
            {
                "escalation": escalation,
                "context": context,
                "agent_id": agent_id,
                "task_id": task_id,
                "session_id": session_id,
            },
        )
        if self.fail:
            msg = "fake park failure"
            raise RuntimeError(msg)


class _FakeForecastRepo:
    """Async CostForecastRepository double for halt-stamp assertions."""

    def __init__(self, forecast: Forecast | None) -> None:
        self._forecast = forecast
        self.saved: list[Forecast] = []

    async def get(self, entity_id: UUID) -> Forecast | None:
        if self._forecast is not None and self._forecast.forecast_id == entity_id:
            return self._forecast
        return None

    async def save(self, entity: Forecast) -> None:
        self.saved.append(entity)


class _MockEngine(AgentEngineErrorsMixin):
    """Minimal mixin host providing the attributes the mixin reads."""

    def __init__(
        self,
        *,
        approval_gate: object | None,
        cost_forecast_repo: object | None = None,
    ) -> None:
        self._approval_gate = approval_gate
        self._cost_tracker = None
        self._cost_forecast_repo = cast(
            "CostForecastRepository | None", cost_forecast_repo
        )


def _forecast(forecast_id: UUID) -> Forecast:
    return Forecast(
        forecast_id=forecast_id,
        brief_hash="a" * 64,
        estimated_cost=0.85,
        lower_bound=0.55,
        upper_bound=1.15,
        currency="USD",
        decision=ForecastDecision.APPROVED,
        decided_at=datetime(2026, 5, 20, 12, 30, tzinfo=UTC),
        decided_by="operator",
        ceiling_amount=1.5,
        created_at=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 20, 12, 30, tzinfo=UTC),
    )


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="Engineer",
        role="Backend Engineer",
        department="Engineering",
        model=ModelConfig(provider="example-provider", model_id="example-medium-001"),
        hiring_date=date(2026, 1, 1),
    )


def _task() -> Task:
    return Task(
        id=as_uuid("task-x"),
        title="Plan a thing",
        description="Plan it carefully.",
        type=TaskType.DEVELOPMENT,
        project="proj-x",
        created_by="op-1",
        hard_ceiling=1.5,
    )


@pytest.mark.asyncio
async def test_hard_ceiling_with_approval_gate_routes_to_parked() -> None:
    """ApprovalGate present + RunHardCeilingExceededError -> PARKED.

    Verifies the engine awaits ``ApprovalGate.park_context()`` and
    routes the termination to PARKED so the operator can raise the
    ceiling and resume.
    """
    gate = _FakeApprovalGate()
    engine = _MockEngine(approval_gate=gate)
    exc = RunHardCeilingExceededError(
        "crossed",
        ceiling_amount=1.5,
        accumulated_cost=1.5,
        currency="USD",
        task_id=sid("task-x"),
    )

    result = await engine._handle_budget_error(
        exc=exc,
        identity=_identity(),
        task=_task(),
        agent_id="agent-1",
        task_id=sid("task-x"),
        duration_seconds=0.1,
    )

    assert result.execution_result.termination_reason is TerminationReason.PARKED
    assert len(gate.calls) == 1
    parked_call = gate.calls[0]
    assert parked_call["agent_id"] == "agent-1"
    assert parked_call["task_id"] == sid("task-x")
    assert (
        cast("EscalationInfo", parked_call["escalation"]).action_type
        == "budget:hard_ceiling_exceeded"
    )


@pytest.mark.asyncio
async def test_park_stamps_forecast_halt_context() -> None:
    """Successful park stamps halt context onto the forecast row."""
    forecast_id = uuid4()
    repo = _FakeForecastRepo(_forecast(forecast_id))
    gate = _FakeApprovalGate()
    engine = _MockEngine(approval_gate=gate, cost_forecast_repo=repo)
    exc = RunHardCeilingExceededError(
        "crossed",
        ceiling_amount=1.5,
        accumulated_cost=1.8,
        currency="USD",
        task_id=sid("task-x"),
        forecast_id=forecast_id,
    )

    await engine._handle_budget_error(
        exc=exc,
        identity=_identity(),
        task=_task(),
        agent_id="agent-1",
        task_id=sid("task-x"),
        duration_seconds=0.1,
    )

    assert len(repo.saved) == 1
    halt = repo.saved[0].halt_context
    assert halt is not None
    assert halt.accumulated_cost == 1.8
    assert halt.ceiling_amount == 1.5
    assert halt.currency == "USD"


@pytest.mark.asyncio
async def test_park_without_forecast_id_skips_stamp() -> None:
    """No forecast id -> no halt stamp, park still routes to PARKED."""
    repo = _FakeForecastRepo(_forecast(uuid4()))
    gate = _FakeApprovalGate()
    engine = _MockEngine(approval_gate=gate, cost_forecast_repo=repo)
    exc = RunHardCeilingExceededError(
        "crossed",
        ceiling_amount=1.5,
        accumulated_cost=1.8,
        currency="USD",
        task_id=sid("task-x"),
    )

    result = await engine._handle_budget_error(
        exc=exc,
        identity=_identity(),
        task=_task(),
        agent_id="agent-1",
        task_id=sid("task-x"),
        duration_seconds=0.1,
    )

    assert result.execution_result.termination_reason is TerminationReason.PARKED
    assert repo.saved == []


@pytest.mark.asyncio
async def test_park_context_failure_falls_back_to_exhausted() -> None:
    """park_context() failure -> degrade to BUDGET_EXHAUSTED (no crash)."""
    gate = _FakeApprovalGate(fail=True)
    engine = _MockEngine(approval_gate=gate)
    exc = RunHardCeilingExceededError(
        "crossed",
        ceiling_amount=1.5,
        accumulated_cost=1.5,
        currency="USD",
    )

    result = await engine._handle_budget_error(
        exc=exc,
        identity=_identity(),
        task=_task(),
        agent_id="agent-1",
        task_id=sid("task-x"),
        duration_seconds=0.1,
    )

    assert (
        result.execution_result.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    )
    assert len(gate.calls) == 1  # park attempted, then logged and degraded


@pytest.mark.asyncio
async def test_hard_ceiling_without_approval_gate_falls_back_to_exhausted() -> None:
    """Missing ApprovalGate -> existing BUDGET_EXHAUSTED termination."""
    engine = _MockEngine(approval_gate=None)
    exc = RunHardCeilingExceededError(
        "crossed",
        ceiling_amount=1.5,
        accumulated_cost=1.5,
        currency="USD",
    )

    result = await engine._handle_budget_error(
        exc=exc,
        identity=_identity(),
        task=_task(),
        agent_id="agent-1",
        task_id=sid("task-x"),
        duration_seconds=0.1,
    )

    assert (
        result.execution_result.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    )


@pytest.mark.asyncio
async def test_other_budget_errors_still_route_to_exhausted() -> None:
    """Non-ceiling budget errors keep the existing BUDGET_EXHAUSTED path."""
    engine = _MockEngine(approval_gate=_FakeApprovalGate())
    exc = BudgetExhaustedError("monthly hard stop crossed")

    result = await engine._handle_budget_error(
        exc=exc,
        identity=_identity(),
        task=_task(),
        agent_id="agent-1",
        task_id=sid("task-x"),
        duration_seconds=0.1,
    )

    assert (
        result.execution_result.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    )
