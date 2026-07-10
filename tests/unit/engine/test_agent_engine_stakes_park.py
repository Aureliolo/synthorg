"""AgentEngine escalates a stakes-tier miss: park when gated, else fail.

When no configured tool-capable model meets a task's stakes tier, the
strategy raises ``StakesModelUnavailableError`` and the engine seam routes
it through ``_handle_stakes_unavailable``. With an ``ApprovalGate`` wired
the run parks (``PARKED``) so an operator can add a stronger provider or
lower the stakes; without one it degrades to the fatal-error boundary
(``FAILED``). The task is never silently run on a sub-tier model.
"""

from datetime import date
from uuid import uuid4

import pytest

from synthorg.approval.models import EscalationInfo
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import Stakes, TaskType
from synthorg.engine.agent_engine_stakes_errors import AgentEngineStakesErrorsMixin
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.routing_policy.errors import StakesModelUnavailableError
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit


class _FakeApprovalGate:
    """Async ApprovalGate double recording park_context calls."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[EscalationInfo] = []
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
        del context, agent_id, task_id, session_id
        self.calls.append(escalation)
        if self.fail:
            msg = "fake park failure"
            raise RuntimeError(msg)


class _MockEngine(AgentEngineStakesErrorsMixin):
    """Minimal mixin host exposing the attributes the mixin reads.

    Only the PARKED and best-effort-park paths are exercised, so
    ``_handle_fatal_error`` (declared on the mixin) is never reached.
    """

    def __init__(self, *, approval_gate: object | None) -> None:
        self._approval_gate = approval_gate  # type: ignore[assignment]
        self._cost_tracker = None


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="Engineer",
        role="Backend Engineer",
        department="Engineering",
        model=ModelConfig(provider="example-provider", model_id="example-small-001"),
        hiring_date=date(2026, 1, 1),
    )


def _task() -> Task:
    return Task(
        id=as_uuid("task-stakes"),
        title="Migrate the production schema",
        description="Run an irreversible production migration.",
        type=TaskType.DEVELOPMENT,
        project="proj-x",
        created_by="op-1",
    )


def _exc() -> StakesModelUnavailableError:
    return StakesModelUnavailableError(stakes=Stakes.CRITICAL, required_tier="large")


@pytest.mark.asyncio
async def test_stakes_unavailable_with_gate_routes_to_parked() -> None:
    """ApprovalGate present -> PARKED with a stakes escalation."""
    gate = _FakeApprovalGate()
    engine = _MockEngine(approval_gate=gate)

    result = await engine._handle_stakes_unavailable(
        exc=_exc(),
        identity=_identity(),
        task=_task(),
        agent_id="agent-1",
        task_id=sid("task-stakes"),
        duration_seconds=0.1,
    )

    assert result.execution_result.termination_reason is TerminationReason.PARKED
    assert len(gate.calls) == 1
    escalation = gate.calls[0]
    assert escalation.action_type == "stakes:model_unavailable"
    assert "large" in escalation.reason
    assert "critical" in escalation.reason


@pytest.mark.asyncio
async def test_park_stakes_unavailable_without_gate_returns_false() -> None:
    """No ApprovalGate -> park is not possible, caller degrades to FAILED."""
    engine = _MockEngine(approval_gate=None)

    parked = await engine._park_stakes_unavailable(
        exc=_exc(),
        identity=_identity(),
        task=_task(),
        agent_id="agent-1",
        task_id=sid("task-stakes"),
        ctx=None,
    )

    assert parked is False


@pytest.mark.asyncio
async def test_park_stakes_unavailable_gate_failure_returns_false() -> None:
    """park_context() failure degrades to False (never crashes the engine)."""
    gate = _FakeApprovalGate(fail=True)
    engine = _MockEngine(approval_gate=gate)

    parked = await engine._park_stakes_unavailable(
        exc=_exc(),
        identity=_identity(),
        task=_task(),
        agent_id="agent-1",
        task_id=sid("task-stakes"),
        ctx=None,
    )

    assert parked is False
    assert len(gate.calls) == 1  # park attempted, then logged and degraded


def test_stakes_model_unavailable_error_carries_stakes_and_tier() -> None:
    """The typed error surfaces the stakes + required tier for the operator."""
    exc = _exc()
    assert exc.stakes is Stakes.CRITICAL
    assert exc.required_tier == "large"
    assert exc.status_code == 503
