"""End-to-end harness for the cost-dial (#1982).

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

The test asserts the acceptance criteria for issue #1982 explicitly so
implementation regressions surface immediately under the harness rather
than via downstream behaviour drift.
"""

import pytest

# Cost-dial modules land across Phases 1 to 7. Until each phase ships the
# imports below remain unresolvable; the module-level skip keeps the
# acceptance spec under version control without breaking the wider suite.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skip(
        reason=(
            "Awaiting #1982 implementation (Phases 1 to 7). "
            "This harness file is the acceptance contract; "
            "individual phases remove the skip as they land."
        ),
    ),
]


@pytest.mark.asyncio
async def test_cost_dial_full_lifecycle_under_simulation_harness() -> None:
    """Full e2e: brief -> forecast -> approve -> run -> ceiling -> resume.

    Acceptance steps mirror the spec in the issue body. Each step is
    asserted explicitly so a regression in any single phase surfaces as
    a targeted failure rather than a diffuse end-state mismatch.
    """
    # Phase 1 (models) + Phase 3 (forecaster) + Phase 4 (entry-gate)
    # + Phase 5 (controllers) + Phase 6 (ceiling) + Phase 7 (Pareto)
    # collectively unblock the body below. Until then the module skip
    # above pre-empts execution.
    pytest.fail("acceptance harness scheduled by #1982 phases 1 to 7")
