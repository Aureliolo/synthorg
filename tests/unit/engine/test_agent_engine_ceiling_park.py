"""AgentEngine park / resume on hard-ceiling exceeded.

When the per-turn ``BudgetChecker`` raises
``RunHardCeilingExceededError`` mid-run, the engine must:

1. Catch the error via the existing ``except BudgetExhaustedError``.
2. Call ``ApprovalGate.park_context`` with
   ``reason="hard_ceiling_exceeded"`` and a payload carrying
   ``accumulated_cost``, ``forecast_id``, and ``ceiling_amount``.
3. Return ``ExecutionResult(termination_reason=PARKED)``.

The resume path (operator raises the ceiling via the API,
``ApprovalGate.resume()`` re-injects the parked context) completes
the task cleanly on the next turn.
"""

import pytest

# Skipped until the AgentEngine catch-handler is extended to route
# RunHardCeilingExceededError through ApprovalGate.park_context.
pytestmark = pytest.mark.skip(
    reason="AgentEngine ceiling -> park / resume wiring not yet landed",
)


@pytest.mark.asyncio
async def test_run_hard_ceiling_exceeded_parks_context() -> None:
    """Ceiling hit -> ApprovalGate.park_context invoked with payload."""
    pytest.fail("engine ceiling handler not landed")


@pytest.mark.asyncio
async def test_run_hard_ceiling_exceeded_returns_parked_termination() -> None:
    """ExecutionResult.termination_reason == TerminationReason.PARKED."""
    pytest.fail("engine ceiling handler not landed")


@pytest.mark.asyncio
async def test_resume_after_raised_ceiling_completes_task() -> None:
    """ApprovalGate.resume() re-enters the loop; task reaches COMPLETED."""
    pytest.fail("engine ceiling handler not landed")


@pytest.mark.asyncio
async def test_parked_payload_carries_accumulated_cost_and_forecast_id() -> None:
    """Parked payload includes accumulated_cost, forecast_id, ceiling_amount."""
    pytest.fail("engine ceiling handler not landed")
