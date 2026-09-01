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

from dataclasses import replace
from pathlib import Path

import pytest

from synthorg.budget.config import BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.tracker import CostTracker
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.park_service import ParkService
from synthorg.engine.resume_message import build_resume_message
from synthorg.providers.models import ToolCall
from synthorg.tools.file_system.write_file import WriteFileTool
from synthorg.tools.registry import ToolRegistry
from tests._shared import (
    UNWIRED_BUDGET,
    as_uuid,
    engine_with,
    unwired_core,
    unwired_governance,
)
from tests.unit.api.fakes import FakeParkedContextRepository

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
        currency=_CURRENCY,
    )


def _with_raised_ceiling(context: AgentContext, ceiling: float) -> AgentContext:
    """Return *context* carrying the ceiling an operator just raised.

    The restored context holds the task as it was when the run parked, so
    resuming it unchanged re-parks on the same ceiling. Production reaches
    the new number through ``ceiling_synced_task`` reading the moved
    forecast row; this is that task, without the repository round trip.

    Returns:
        The context with its task's ``hard_ceiling`` replaced.

    Raises:
        AssertionError: If the context carries no task execution, which a
            parked run always does.
    """
    execution = context.task_execution
    assert execution is not None
    raised = execution.task.model_copy(update={"hard_ceiling": ceiling})
    return context.model_copy(
        update={"task_execution": execution.model_copy(update={"task": raised})}
    )


def _approval_gate(repo: FakeParkedContextRepository) -> ApprovalGate:
    # A real repository, because a park with nowhere to store the context is
    # refused: it would report PARKED and leave the resume nothing to find.
    # Taken as an argument so both legs share one, which is what makes the
    # parked context reachable from the second: a gate per leg parks into a
    # store the resume never looks at, and PARKED then means only that the
    # gate was asked, never that anything survived.
    return ApprovalGate(
        park_service=ParkService(),
        parked_context_repo=repo,
    )


async def test_hard_ceiling_run_parks_then_resumes(e2e_workspace: Path) -> None:
    write_tool = WriteFileTool(workspace_root=e2e_workspace)
    registry = ToolRegistry([write_tool])
    cost_tracker = CostTracker()
    enforcer = BudgetEnforcer(
        # The per-run ceiling under test comes from ``Task.hard_ceiling``
        # (set below via model_copy); ``run_hard_ceiling`` is only the
        # fallback used when the task carries none, so it stays 0.0 (unused).
        budget_config=_budget_config(run_hard_ceiling=0.0),
        cost_tracker=cost_tracker,
    )

    identity = make_e2e_identity()
    forecast_id = as_uuid("forecast-budget-ceiling")
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
    parked_contexts = FakeParkedContextRepository()
    gate = _approval_gate(parked_contexts)
    engine = engine_with(
        over_budget,
        core=replace(unwired_core(over_budget), tool_registry=registry),
        budget=replace(UNWIRED_BUDGET, budget_enforcer=enforcer),
        governance=replace(unwired_governance(), approval_gate=gate),
    )
    task = make_e2e_task(identity=identity, title="Over-budget run").model_copy(
        update={"hard_ceiling": _HARD_CEILING, "forecast_id": forecast_id},
    )

    parked = await engine.run(identity=identity, task=task, max_turns=5)

    # The ceiling crossing parks the run (resumable), not a hard failure.
    assert parked.termination_reason is TerminationReason.PARKED

    # PARKED is the engine's own verdict; this is the part an operator
    # depends on. The context has to be in the store, and the resume path
    # has to be able to load and deserialise it, or the run is stuck with a
    # status that says otherwise.
    stored = await parked_contexts.list_items()
    assert len(stored) == 1
    approval_id = stored[0].approval_id
    recovered = await gate.resume_context(approval_id)
    assert recovered is not None
    assert not await parked_contexts.list_items()
    parked_context, _ = recovered

    # Resume leg: the operator raised the ceiling and approved. The run
    # continues from the context that was parked, through the engine's own
    # resume entry point -- a fresh ``run`` on a new task would prove only
    # that a new run completes under a higher ceiling, which it would do
    # whether or not anything was ever parked.
    #
    # The raised ceiling reaches the run on the restored task, which is what
    # ``ceiling_synced_task`` produces from the moved forecast row; that
    # hand-off itself is covered by ``tests/integration/test_cost_dial_e2e.py``.
    resume_engine = engine_with(
        ScriptedProvider([make_text_response("All done.")]),
        core=replace(
            unwired_core(ScriptedProvider([make_text_response("All done.")])),
            tool_registry=registry,
        ),
        budget=replace(
            UNWIRED_BUDGET,
            budget_enforcer=BudgetEnforcer(
                budget_config=_budget_config(run_hard_ceiling=0.0),
                # Reuse the leg-1 tracker so the resume enforces the raised
                # ceiling against the CUMULATIVE spend carried over from the
                # parked run, not a fresh zero -- otherwise the COMPLETED
                # assertion would hold regardless of whether the raise took
                # effect.
                cost_tracker=cost_tracker,
            ),
        ),
        governance=replace(unwired_governance(), approval_gate=gate),
    )
    resumed = await resume_engine.resume_parked_run(
        parked_context=_with_raised_ceiling(parked_context, _RAISED_CEILING),
        approval_id=approval_id,
        decision_message=build_resume_message(
            approval_id,
            approved=True,
            decided_by="operator",
            decision_reason="ceiling raised",
        ),
        approved=True,
    )
    assert resumed.termination_reason is TerminationReason.COMPLETED
