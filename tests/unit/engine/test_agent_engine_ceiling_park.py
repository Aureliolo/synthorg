"""AgentEngine routes RunHardCeilingExceededError to PARKED.

When the per-turn ``BudgetChecker`` raises
``RunHardCeilingExceededError`` mid-run, the engine catches it via the
existing ``except BudgetExhaustedError`` handler and routes to
``TerminationReason.PARKED`` (instead of the default
``BUDGET_EXHAUSTED``) when an ``ApprovalGate`` is wired. This unit
test exercises the routing directly against ``_handle_budget_error``.

The actual ``ApprovalGate.park_context()`` integration (persistent
state for the operator-resume flow) is a follow-up; this test
verifies the in-process termination shape that the resume path
needs.
"""

from datetime import date
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from synthorg.budget.errors import (
    BudgetExhaustedError,
    RunHardCeilingExceededError,
)
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import TaskType
from synthorg.core.task import Task
from synthorg.engine.agent_engine_errors import AgentEngineErrorsMixin
from synthorg.engine.loop_protocol import TerminationReason


class _MockEngine(AgentEngineErrorsMixin):
    """Minimal mixin host providing the attributes the mixin reads."""

    def __init__(self, *, approval_gate: object | None) -> None:
        self._approval_gate = approval_gate
        self._cost_tracker = None  # type: ignore[assignment]


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
        id="task-x",
        title="Plan a thing",
        description="Plan it carefully.",
        type=TaskType.DEVELOPMENT,
        project="proj-x",
        created_by="op-1",
        hard_ceiling=1.5,
    )


@pytest.mark.asyncio
async def test_hard_ceiling_with_approval_gate_routes_to_parked() -> None:
    """ApprovalGate present + RunHardCeilingExceededError -> PARKED."""
    engine = _MockEngine(approval_gate=SimpleNamespace(park_context=lambda **_: None))
    exc = RunHardCeilingExceededError(
        "crossed",
        ceiling_amount=1.5,
        accumulated_cost=1.5,
        currency="USD",
    )

    result = cast(
        "Any",
        engine._handle_budget_error(
            exc=exc,
            identity=_identity(),
            task=_task(),
            agent_id="agent-1",
            task_id="task-x",
            duration_seconds=0.1,
        ),
    )

    assert result.execution_result.termination_reason is TerminationReason.PARKED


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

    result = cast(
        "Any",
        engine._handle_budget_error(
            exc=exc,
            identity=_identity(),
            task=_task(),
            agent_id="agent-1",
            task_id="task-x",
            duration_seconds=0.1,
        ),
    )

    assert (
        result.execution_result.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    )


@pytest.mark.asyncio
async def test_other_budget_errors_still_route_to_exhausted() -> None:
    """Non-ceiling budget errors keep the existing BUDGET_EXHAUSTED path."""
    engine = _MockEngine(approval_gate=SimpleNamespace(park_context=lambda **_: None))
    exc = BudgetExhaustedError("monthly hard stop crossed")

    result = cast(
        "Any",
        engine._handle_budget_error(
            exc=exc,
            identity=_identity(),
            task=_task(),
            agent_id="agent-1",
            task_id="task-x",
            duration_seconds=0.1,
        ),
    )

    assert (
        result.execution_result.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    )
