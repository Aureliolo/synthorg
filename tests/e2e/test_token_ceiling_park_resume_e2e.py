"""Acceptance: a flat-rate run that crosses its TOKEN ceiling parks, then resumes.

The money twin of this test cannot cover the case the token ceiling exists
for. Against a provider that bills by flat subscription every call records
``cost=0.0``, so the money ceiling can never fire and the run's only
remaining bound is its turn cap. Here the provider reports real token usage
and zero cost, exactly as a flat-rate connection does, and the token ceiling
is the only thing that can stop it.

End-to-end through the REAL ``AgentEngine`` loop, no mock on the seam under
test: a real ``BudgetEnforcer`` builds the per-turn checker, a real
``ApprovalGate`` with a real (in-memory) parked-context repository routes the
crossing to ``PARKED``, and the resume leg runs the same engine with the
ceiling raised through the one route an operator has.

Zero real LLM spend: the provider is scripted with fixed token usage.
"""

from pathlib import Path

import pytest

from synthorg.budget.config import BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.tracker import CostTracker
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.park_service import ParkService
from synthorg.engine.resume_message import build_resume_message
from synthorg.providers.models import CompletionResponse, ToolCall
from synthorg.tools.file_system.write_file import WriteFileTool
from synthorg.tools.registry import ToolRegistry
from tests.unit.api.fakes import FakeParkedContextRepository

from .conftest import (
    ScriptedProvider,
    make_e2e_identity,
    make_e2e_task,
    make_text_response,
    make_tool_call_response,
)

pytestmark = pytest.mark.e2e

# A flat-rate turn: real token usage, zero cost, which is exactly what a
# provider billing by flat subscription records. 1500 tokens per turn, so a
# ceiling of 1000 is crossed by the first and 1_000_000 by neither.
_INPUT_TOKENS = 1_000
_OUTPUT_TOKENS = 500
_FLAT_RATE_COST = 0.0
_TOKEN_CEILING = 1_000
_RAISED_TOKEN_CEILING = 1_000_000


def _flat_rate_tool_turn() -> CompletionResponse:
    """A tool-call turn that accrues tokens and no cost."""
    return make_tool_call_response(
        tool_calls=(
            ToolCall(
                id="call-1",
                name="write_file",
                arguments={"path": "out.txt", "content": "turn 1"},
            ),
        ),
        input_tokens=_INPUT_TOKENS,
        output_tokens=_OUTPUT_TOKENS,
        cost=_FLAT_RATE_COST,
    )


def _flat_rate_text_turn(content: str) -> CompletionResponse:
    """A finishing turn that accrues tokens and no cost."""
    return make_text_response(
        content,
        input_tokens=_INPUT_TOKENS,
        output_tokens=_OUTPUT_TOKENS,
        cost=_FLAT_RATE_COST,
    )


def _budget_config(*, run_hard_token_ceiling: int) -> BudgetConfig:
    return BudgetConfig(
        total_monthly=0.0,
        # Every money bound off, so nothing but the token ceiling can halt
        # the run: that is the state a flat-rate estate is actually in.
        per_task_limit=0.0,
        per_agent_daily_limit=0.0,
        run_hard_ceiling=0.0,
        run_hard_token_ceiling=run_hard_token_ceiling,
        forecast_required=False,
    )


def _approval_gate(repo: FakeParkedContextRepository) -> ApprovalGate:
    # Taken as an argument so both legs share one store: a gate per leg parks
    # into somewhere the resume never looks, and PARKED then means only that
    # the gate was asked, never that anything survived to resume from.
    return ApprovalGate(
        park_service=ParkService(),
        parked_context_repo=repo,
    )


def _engine(
    *,
    provider: ScriptedProvider,
    registry: ToolRegistry,
    cost_tracker: CostTracker,
    run_hard_token_ceiling: int,
    gate: ApprovalGate,
) -> AgentEngine:
    return AgentEngine(
        provider=provider,
        tool_registry=registry,
        budget_enforcer=BudgetEnforcer(
            budget_config=_budget_config(run_hard_token_ceiling=run_hard_token_ceiling),
            cost_tracker=cost_tracker,
        ),
        approval_gate=gate,
    )


async def test_token_ceiling_run_parks_then_resumes(e2e_workspace: Path) -> None:
    write_tool = WriteFileTool(workspace_root=e2e_workspace)
    registry = ToolRegistry([write_tool])
    cost_tracker = CostTracker()
    identity = make_e2e_identity()

    # First turn calls a tool (accrues tokens, keeps the loop going); the
    # next turn's pre-check sees the accrued tokens cross the ceiling.
    over_budget = ScriptedProvider(
        [
            _flat_rate_tool_turn(),
            _flat_rate_text_turn("Should not finish -- the token ceiling trips."),
        ]
    )
    parked_contexts = FakeParkedContextRepository()
    gate = _approval_gate(parked_contexts)
    engine = _engine(
        provider=over_budget,
        registry=registry,
        cost_tracker=cost_tracker,
        run_hard_token_ceiling=_TOKEN_CEILING,
        gate=gate,
    )
    task = make_e2e_task(identity=identity, title="Flat-rate run")

    parked = await engine.run(identity=identity, task=task, max_turns=5)

    # PARKED, not BUDGET_EXHAUSTED: the operator can raise the ceiling and
    # carry on, which is the whole point of parking rather than stopping.
    assert parked.termination_reason is TerminationReason.PARKED

    # PARKED is the engine's verdict; this is what the operator depends on.
    # The context has to be in the store and the resume path has to be able
    # to load and deserialise it, or the run is stuck under a status that
    # says otherwise.
    stored = await parked_contexts.list_items()
    assert len(stored) == 1
    approval_id = stored[0].approval_id
    recovered = await gate.resume_context(approval_id)
    assert recovered is not None
    assert not await parked_contexts.list_items()
    parked_context, _ = recovered

    # Resume leg: the operator raised budget.run_hard_token_ceiling, which is
    # the route the parked approval's reason names, and approved. The run
    # continues from the context that was parked, through the engine's own
    # resume entry point -- a fresh run on a new task would complete under
    # the raised ceiling whether or not anything was ever parked. The same
    # cost tracker is reused so the raised ceiling is enforced against the
    # tokens already accumulated rather than a fresh zero.
    resumed = await _engine(
        provider=ScriptedProvider([_flat_rate_text_turn("All done.")]),
        registry=registry,
        cost_tracker=cost_tracker,
        run_hard_token_ceiling=_RAISED_TOKEN_CEILING,
        gate=gate,
    ).resume_parked_run(
        parked_context=parked_context,
        approval_id=approval_id,
        decision_message=build_resume_message(
            approval_id,
            approved=True,
            decided_by="operator",
            decision_reason="token ceiling raised",
        ),
        approved=True,
    )
    assert resumed.termination_reason is TerminationReason.COMPLETED


async def test_the_same_run_is_unbounded_without_the_token_ceiling(
    e2e_workspace: Path,
) -> None:
    """The money ceiling alone cannot stop this run, which is the defect.

    Same provider, same zero cost, every money bound configured: the run
    goes to completion because there is nothing for a cost ceiling to
    measure. Asserting it here is what makes the test above evidence rather
    than a coincidence.
    """
    registry = ToolRegistry([WriteFileTool(workspace_root=e2e_workspace)])
    identity = make_e2e_identity()
    engine = AgentEngine(
        provider=ScriptedProvider(
            [_flat_rate_tool_turn(), _flat_rate_text_turn("Finished, unbounded.")]
        ),
        tool_registry=registry,
        budget_enforcer=BudgetEnforcer(
            budget_config=BudgetConfig(
                total_monthly=0.0,
                # A money ceiling far below anything a metered run would
                # reach, and still never crossed: cost stays 0.0.
                run_hard_ceiling=0.000_1,
                run_hard_token_ceiling=0,
                forecast_required=False,
            ),
            cost_tracker=CostTracker(),
        ),
        approval_gate=_approval_gate(FakeParkedContextRepository()),
    )

    result = await engine.run(
        identity=identity,
        task=make_e2e_task(identity=identity, title="Unbounded flat-rate run"),
        max_turns=5,
    )
    assert result.termination_reason is TerminationReason.COMPLETED
