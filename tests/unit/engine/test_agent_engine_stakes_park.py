"""AgentEngine escalates a stakes-tier miss: park when gated, else fail.

When no configured tool-capable model meets a task's stakes tier, the
strategy raises ``StakesModelUnavailableError`` and the engine seam routes
it through ``_handle_stakes_unavailable``. With an ``ApprovalGate`` wired
the run parks (``PARKED``) so an operator can add a stronger provider or
lower the stakes; without one it degrades to the fatal-error boundary
(``FAILED``). The task is never silently run on a sub-tier model.
"""

from datetime import date
from typing import override

import pytest

from synthorg.approval.models import EscalationInfo
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.core.task import Task
from synthorg.core.task_enums import Stakes, TaskType
from synthorg.engine.agent_engine_stakes_errors import AgentEngineStakesErrorsMixin
from synthorg.engine.context import AgentContext
from synthorg.engine.cost_recording import resolve_tracker_currency
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.prompt import SystemPrompt, build_error_prompt
from synthorg.engine.routing_policy.errors import StakesModelUnavailableError
from synthorg.engine.run_result import AgentRunResult
from synthorg.providers.models import CompletionConfig
from synthorg.providers.protocol import CompletionProvider
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
    """Minimal mixin host exposing the attributes and the fatal-error seam."""

    def __init__(self, *, approval_gate: object | None) -> None:
        # A concrete ApprovalGate double: structurally matched, not a subclass.
        self._approval_gate = approval_gate  # type: ignore[assignment]
        self._cost_tracker = None
        self.fatal_calls = 0

    @override
    async def _handle_fatal_error(
        self,
        *,
        exc: Exception,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        duration_seconds: float,
        ctx: AgentContext | None = None,
        system_prompt: SystemPrompt | None = None,
        completion_config: CompletionConfig | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
        provider: CompletionProvider | None = None,
    ) -> AgentRunResult:
        """Record the fatal-error fallback and return an ERROR result."""
        del completion_config, effective_autonomy, provider
        self.fatal_calls += 1
        error_ctx = ctx or AgentContext.from_identity(identity, task=task)
        return AgentRunResult(
            execution_result=ExecutionResult(
                context=error_ctx,
                termination_reason=TerminationReason.ERROR,
                error_message=type(exc).__name__,
            ),
            system_prompt=build_error_prompt(identity, agent_id, system_prompt),
            duration_seconds=duration_seconds,
            agent_id=agent_id,
            task_id=task_id,
            currency=resolve_tracker_currency(None),
        )


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=as_uuid("engineer"),
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
    assert engine.fatal_calls == 0
    assert len(gate.calls) == 1
    escalation = gate.calls[0]
    assert escalation.action_type == "stakes:model_unavailable"
    assert "large" in escalation.reason
    assert "critical" in escalation.reason


async def test_stakes_unavailable_without_gate_falls_to_fatal_error() -> None:
    """No ApprovalGate -> degrades to the fatal-error boundary (FAILED path)."""
    engine = _MockEngine(approval_gate=None)

    result = await engine._handle_stakes_unavailable(
        exc=_exc(),
        identity=_identity(),
        task=_task(),
        agent_id="agent-1",
        task_id=sid("task-stakes"),
        duration_seconds=0.1,
    )

    # The run is never parked; it reaches _handle_fatal_error exactly once.
    assert engine.fatal_calls == 1
    assert result.execution_result.termination_reason is TerminationReason.ERROR


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
