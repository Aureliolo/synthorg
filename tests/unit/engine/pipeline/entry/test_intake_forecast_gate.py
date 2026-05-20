"""Forecast gate at the work-entry adapter seam.

The intake / task-board / objective entry adapters must consult the
``CostForecaster`` and ``CostForecastRepository`` before dispatching
into the work pipeline.

Branching:

* ``budget.forecast_required=True`` and no ``Task.forecast_id``
  -> raise ``CostForecastApprovalRequiredError`` (HTTP 402); controller
  returns the forecast payload to the operator.
* ``Task.forecast_id`` maps to a row with ``decision=pending``
  -> same error (still awaiting decision).
* ``Task.forecast_id`` maps to ``decision=approved``
  -> dispatch into the work pipeline normally.
* ``Task.forecast_id`` maps to ``decision=rejected``
  -> terminal typed error; the work item never runs.
* ``Task.forecast_id`` maps to ``decision=superseded``
  -> same as missing (operator edited the brief; needs fresh approval).
* ``budget.forecast_required=False``
  -> short-circuit: dispatch without forecast lookup.
"""

import pytest

# Skipped until the entry adapters are extended to consult the
# forecast gate; the standalone ForecastGate unit tests cover the
# branching logic in tests/unit/engine/pipeline/test_forecast_gate.py.
pytestmark = pytest.mark.skip(
    reason="entry-adapter forecast-gate integration not yet wired",
)


@pytest.mark.asyncio
async def test_forecast_required_with_no_forecast_id_raises() -> None:
    """CostForecastApprovalRequiredError when forecast_required and no id."""
    pytest.fail("entry-adapter wiring not landed")


@pytest.mark.asyncio
async def test_pending_forecast_blocks_dispatch() -> None:
    """Task.forecast_id -> pending row -> CostForecastApprovalRequiredError."""
    pytest.fail("entry-adapter wiring not landed")


@pytest.mark.asyncio
async def test_approved_forecast_dispatches_into_work_pipeline() -> None:
    """approved row -> work_pipeline.execute() is invoked."""
    pytest.fail("entry-adapter wiring not landed")


@pytest.mark.asyncio
async def test_rejected_forecast_terminates_work_item() -> None:
    """rejected row -> terminal typed error; pipeline is not called."""
    pytest.fail("entry-adapter wiring not landed")


@pytest.mark.asyncio
async def test_superseded_forecast_requires_fresh_approval() -> None:
    """superseded row -> CostForecastApprovalRequiredError (treat as missing)."""
    pytest.fail("entry-adapter wiring not landed")


@pytest.mark.asyncio
async def test_forecast_disabled_short_circuits_lookup() -> None:
    """forecast_required=False -> dispatch without consulting forecaster."""
    pytest.fail("entry-adapter wiring not landed")
