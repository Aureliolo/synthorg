"""End-to-end harness for the cost dial.

Validates the full operator-facing flow under the simulation harness:

1. Submit a brief via the work-entry adapter.
2. A pre-flight ``CostForecast`` row is created with ``decision=pending``;
   the work pipeline refuses to dispatch the task.
3. Operator approves the forecast via the API.
4. Work pipeline dispatches; agents execute.
5. The accumulated cost crosses the per-brief hard ceiling.
6. ``AgentEngine`` catches ``RunHardCeilingExceededError`` and parks
   the context via ``ApprovalGate``; ``ExecutionResult.termination_reason``
   is ``PARKED``; the dashboard surfaces a halted run.
7. Operator raises the ceiling via the API; ``ApprovalGate.resume()``
   re-enters the execution loop and the task completes cleanly.
8. ``GET /budget/pareto`` returns a frontier that references the roles
   that ran, with each ``ParetoPoint`` carrying the benchmark ``source``.

The test asserts the acceptance criteria for the cost-dial feature
explicitly so implementation regressions surface immediately under
the harness rather than via downstream behaviour drift.
"""

import pytest

# Acceptance harness is skipped until the cost-dial controllers,
# approval routing, and engine park / resume wiring all land.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skip(
        reason=(
            "Cost-dial e2e acceptance harness; unskipped once controllers"
            " + approval routing + engine park / resume are wired."
        ),
    ),
]


@pytest.mark.asyncio
async def test_cost_dial_full_lifecycle_under_simulation_harness() -> None:
    """Full e2e: brief -> forecast -> approve -> run -> ceiling -> resume.

    Each acceptance step is asserted explicitly so a regression in any
    single component surfaces as a targeted failure rather than a
    diffuse end-state mismatch.
    """
    pytest.fail("acceptance harness not yet wired to controllers + engine")
