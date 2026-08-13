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

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import JsonValue

from synthorg.approval.models import EscalationInfo
from synthorg.budget.errors import (
    BudgetExhaustedError,
    RunHardCeilingExceededError,
    RunHardTokenCeilingExceededError,
)
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.clock import Clock
from synthorg.core.persistence_errors import QueryError
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine._ceiling_sync import ceiling_synced_task
from synthorg.engine.agent_engine_errors import AgentEngineErrorsMixin
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.persistence.cost_forecast_protocol import (
    CostForecastFilterSpec,
    CostForecastRepository,
)
from tests._shared import FakeClock, as_uuid, sid

pytestmark = pytest.mark.unit

#: What the engine's clock reads while a ceiling halt is stamped, so the
#: assertion names the instant the test set rather than wall-clock now.
_HALTED_AT = datetime(2026, 5, 20, 13, 0, tzinfo=UTC)


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


class _ForecastRepoSurface:
    """The protocol members these doubles never exercise.

    ``ceiling_synced_task`` takes ``CostForecastRepository`` at a typed
    boundary, so typeguard resolves the whole protocol against whatever
    is passed. Only ``get`` and ``save`` carry behaviour worth asserting
    on; the rest exist so the doubles satisfy the protocol.
    """

    async def delete(self, entity_id: UUID) -> bool:
        return False

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[Forecast, ...]:
        return ()

    async def transition_if(
        self,
        entity_id: UUID,
        from_state: ForecastDecision,
        to_state: ForecastDecision,
        **updates: object,
    ) -> bool:
        return False

    async def raise_ceiling_if_halted(
        self,
        entity_id: UUID,
        *,
        new_ceiling: float,
        updated_at: datetime,
    ) -> bool:
        return False

    async def claim_if_unclaimed(
        self,
        entity_id: UUID,
        *,
        gated_work_item: Mapping[str, JsonValue],
        brief_hash: NotBlankStr,
        updated_at: datetime,
    ) -> bool:
        return False

    async def query(
        self,
        filter_spec: CostForecastFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Forecast, ...]:
        return ()

    async def count(self, filter_spec: CostForecastFilterSpec) -> int:
        return 0


class _FakeForecastRepo(_ForecastRepoSurface):
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


class _ExplodingForecastRepo(_ForecastRepoSurface):
    """CostForecastRepository double whose reads always fail."""

    async def get(self, entity_id: UUID) -> Forecast | None:
        msg = f"forecast {entity_id} unreadable"
        raise QueryError(msg)

    async def save(self, entity: Forecast) -> None:
        msg = f"forecast {entity.forecast_id} unwritable"
        raise QueryError(msg)


class _MockEngine(AgentEngineErrorsMixin):
    """Minimal mixin host providing the attributes the mixin reads."""

    def __init__(
        self,
        *,
        approval_gate: object | None,
        cost_forecast_repo: object | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._approval_gate = cast("ApprovalGate | None", approval_gate)
        self._cost_tracker = None
        self._cost_forecast_repo = cast(
            "CostForecastRepository | None", cost_forecast_repo
        )
        # The halt stamp reads both timestamps through the engine's seam, so
        # a host without one cannot stamp at all.
        self._clock = clock or FakeClock(start=_HALTED_AT)


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
        model=ModelConfig(provider="example-provider", model_id="example-capable-001"),
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
async def test_raised_forecast_ceiling_reaches_enforcement() -> None:
    """An operator's raised ceiling overrides the intake task snapshot.

    ``Task.hard_ceiling`` is the value captured at intake and is never
    rewritten, while ``raise_ceiling`` moves the forecast row. Reading
    the task alone would re-park a resumed run on the very ceiling that
    stopped it.
    """
    forecast_id = as_uuid("forecast-raised-ceiling")
    raised = _forecast(forecast_id).model_copy(update={"ceiling_amount": 5.0})
    repo = cast("CostForecastRepository", _FakeForecastRepo(raised))

    stale = _task().model_copy(update={"forecast_id": forecast_id})
    synced = await ceiling_synced_task(stale, repo)

    assert stale.hard_ceiling == 1.5
    assert synced.hard_ceiling == 5.0


@pytest.mark.asyncio
async def test_lower_forecast_ceiling_leaves_the_task_alone() -> None:
    """A forecast below the snapshot never loosens or tightens the run.

    ``ceiling_synced_task`` exists to carry an operator's raise through to
    enforcement. Adopting a lower value instead would log it as a raise and
    hand the run a limit the operator never asked it to obey mid-flight.
    """
    forecast_id = as_uuid("forecast-lowered-ceiling")
    lowered = _forecast(forecast_id).model_copy(update={"ceiling_amount": 0.5})
    repo = cast("CostForecastRepository", _FakeForecastRepo(lowered))

    stale = _task().model_copy(update={"forecast_id": forecast_id})
    synced = await ceiling_synced_task(stale, repo)

    assert synced.hard_ceiling == 1.5


@pytest.mark.asyncio
async def test_unlinked_task_skips_the_forecast_read() -> None:
    """No forecast repo and no forecast id means nothing to reconcile.

    Both are optional at boot, so the guard is the common path for a run
    that never went through the forecast gate.
    """
    plain = _task()
    repo = cast("CostForecastRepository", _FakeForecastRepo(None))

    assert await ceiling_synced_task(plain, None) is plain
    assert await ceiling_synced_task(plain, repo) is plain


@pytest.mark.asyncio
async def test_missing_forecast_row_keeps_the_snapshot() -> None:
    """A linked forecast that no longer resolves leaves the task alone."""
    repo = cast("CostForecastRepository", _FakeForecastRepo(None))
    stale = _task().model_copy(update={"forecast_id": as_uuid("forecast-gone")})

    synced = await ceiling_synced_task(stale, repo)

    assert synced.hard_ceiling == 1.5


@pytest.mark.asyncio
async def test_unreadable_forecast_keeps_the_stricter_snapshot() -> None:
    """A forecast read failure enforces the task snapshot, not no ceiling.

    The snapshot is the lower of the two, so degrading to it re-parks the
    run rather than letting it spend past a limit nobody raised.
    """
    repo = cast("CostForecastRepository", _ExplodingForecastRepo())
    stale = _task().model_copy(update={"forecast_id": as_uuid("forecast-unreadable")})

    synced = await ceiling_synced_task(stale, repo)

    assert synced.hard_ceiling == 1.5


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
async def test_token_ceiling_parks_and_says_how_to_raise_it() -> None:
    """A token crossing parks under the same action type, with a way out.

    Money and tokens are the same event, "a run halted on a ceiling", so a
    second action type would only be a fresh entry in the autonomy taxonomy.
    What differs is the way out: there is no forecast row to raise a token
    ceiling through, so the parked approval names the two settings that do.
    A park with no route out would be a fresh deadlock.
    """
    gate = _FakeApprovalGate()
    engine = _MockEngine(approval_gate=gate)
    exc = RunHardTokenCeilingExceededError(
        "crossed",
        token_ceiling=1_000,
        tokens_used=1_002,
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
    escalation = cast("EscalationInfo", gate.calls[0]["escalation"])
    assert escalation.action_type == "budget:hard_ceiling_exceeded"
    assert "1002 tokens" in escalation.reason
    assert "budget.run_hard_token_ceiling" in escalation.reason
    # Both routes named, and both reachable: the per-task one names the
    # endpoint that raises it, because an instruction pointing at a field with
    # no write path parks the run with no way out.
    assert "hard_token_ceiling (PATCH /tasks/{id})" in escalation.reason


@pytest.mark.asyncio
async def test_token_ceiling_stamps_no_forecast_halt() -> None:
    """A forecast estimates money and has nothing to say about tokens.

    Stamping one would be a halt context claiming a currency for a token
    count: a record that reads true and is not.
    """
    repo = _FakeForecastRepo(_forecast(as_uuid("forecast-token")))
    gate = _FakeApprovalGate()
    engine = _MockEngine(approval_gate=gate, cost_forecast_repo=repo)

    await engine._handle_budget_error(
        exc=RunHardTokenCeilingExceededError(
            "crossed",
            token_ceiling=1_000,
            tokens_used=1_000,
            task_id=sid("task-x"),
        ),
        identity=_identity(),
        task=_task(),
        agent_id="agent-1",
        task_id=sid("task-x"),
        duration_seconds=0.1,
    )

    assert repo.saved == []


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
    # Read through the engine's seam, so the stamp lands at the instant the
    # test set rather than at wall-clock now.
    assert halt.halted_at == _HALTED_AT
    assert repo.saved[0].updated_at == _HALTED_AT


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
