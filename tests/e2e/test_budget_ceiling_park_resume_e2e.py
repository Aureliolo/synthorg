"""Acceptance: a run that crosses its hard ceiling PARKS, then resumes.

End-to-end through the REAL ``AgentEngine`` loop, no mock on the seam
under test:

* a real ``BudgetEnforcer`` builds the per-turn hard-ceiling checker from
  ``Task.hard_ceiling``,
* a scripted, deterministic provider drives cost above the ceiling (a
  tool-call turn that accrues spend, then a turn whose pre-check trips),
* a real ``ApprovalGate`` so the engine routes the ceiling crossing to
  ``TerminationReason.PARKED`` (operator-resumable) rather than a hard
  ``BUDGET_EXHAUSTED`` / ``ERROR`` failure,
* the resume leg re-runs the same engine with the ceiling raised and the
  run completes cleanly.

Zero real LLM spend: the provider is scripted with fixed token usage.
"""

from pathlib import Path
from uuid import uuid4

import pytest

from synthorg.budget.config import AutoDowngradeConfig, BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.tracker import CostTracker
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.park_service import ParkService
from synthorg.providers.models import ToolCall
from synthorg.tools.file_system.write_file import WriteFileTool
from synthorg.tools.registry import ToolRegistry

from .conftest import (
    ScriptedProvider,
    make_e2e_identity,
    make_e2e_task,
    make_text_response,
    make_tool_call_response,
)

pytestmark = pytest.mark.e2e

_CURRENCY = "USD"
# Tool-call turn cost (0.005) exceeds the ceiling but a single text turn
# (0.008) on resume stays under the raised ceiling.
_HARD_CEILING = 0.004
_RAISED_CEILING = 1.0


def _budget_config(*, run_hard_ceiling: float) -> BudgetConfig:
    return BudgetConfig(
        total_monthly=0.0,
        run_hard_ceiling=run_hard_ceiling,
        forecast_required=False,
        auto_downgrade=AutoDowngradeConfig(enabled=False),
        currency=_CURRENCY,
    )


def _approval_gate() -> ApprovalGate:
    # parked_context_repo is optional; the park still routes to PARKED
    # without a persistence backend wired.
    return ApprovalGate(park_service=ParkService())


async def test_hard_ceiling_run_parks_then_resumes(e2e_workspace: Path) -> None:
    write_tool = WriteFileTool(workspace_root=e2e_workspace)
    registry = ToolRegistry([write_tool])
    cost_tracker = CostTracker()
    enforcer = BudgetEnforcer(
        budget_config=_budget_config(run_hard_ceiling=0.0),
        cost_tracker=cost_tracker,
    )

    identity = make_e2e_identity()
    forecast_id = uuid4()
    # The first turn calls a tool (accrues cost, keeps the loop going); the
    # next turn's budget pre-check sees the accrued cost cross the ceiling.
    over_budget = ScriptedProvider(
        [
            make_tool_call_response(
                tool_calls=(
                    ToolCall(
                        id="call-1",
                        name="write_file",
                        arguments={"path": "out.txt", "content": "turn 1"},
                    ),
                ),
            ),
            make_text_response("Should not finish -- ceiling trips first."),
        ]
    )
    engine = AgentEngine(
        provider=over_budget,
        tool_registry=registry,
        budget_enforcer=enforcer,
        approval_gate=_approval_gate(),
    )
    task = make_e2e_task(identity=identity, title="Over-budget run").model_copy(
        update={"hard_ceiling": _HARD_CEILING, "forecast_id": forecast_id},
    )

    parked = await engine.run(identity=identity, task=task, max_turns=5)

    # The ceiling crossing parks the run (resumable), not a hard failure.
    assert parked.termination_reason is TerminationReason.PARKED

    # Resume leg: operator raised the ceiling; the run now completes.
    resume_provider = ScriptedProvider([make_text_response("All done.")])
    resume_engine = AgentEngine(
        provider=resume_provider,
        tool_registry=registry,
        budget_enforcer=BudgetEnforcer(
            budget_config=_budget_config(run_hard_ceiling=0.0),
            cost_tracker=CostTracker(),
        ),
        approval_gate=_approval_gate(),
    )
    resumed_task = make_e2e_task(identity=identity, title="Resumed run").model_copy(
        update={"hard_ceiling": _RAISED_CEILING, "forecast_id": forecast_id},
    )
    resumed = await resume_engine.run(identity=identity, task=resumed_task, max_turns=5)
    assert resumed.termination_reason is TerminationReason.COMPLETED
