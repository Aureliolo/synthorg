"""Unit tests for :class:`ForecastGateRedispatcher`.

The gate refuses a brief and keeps the work item. These cover the other
half of that bargain: an approval actually runs what was kept, and a
payload that no longer parses is refused loudly rather than dropped.
"""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import JsonValue

from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.engine.pipeline.forecast_redispatch import ForecastGateRedispatcher
from tests._shared import StubWorkPipeline, as_uuid

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)

_WORK_ITEM: dict[str, JsonValue] = {
    "origin_adapter_id": "objective-entry",
    "source": "objective",
    "title": "Ship the game",
    "raw_intent": "A playable Tetris with a browser front end",
    "project": "tetris",
    "requested_by": "operator",
    "correlation_id": "corr-001",
}


def _forecast(
    *,
    gated_work_item: dict[str, JsonValue] | None,
    forecast_id: UUID | None = None,
    ceiling_amount: float | None = 2.0,
) -> Forecast:
    return Forecast(
        forecast_id=forecast_id or as_uuid("approved-forecast"),
        brief_hash="a" * 64,
        estimated_cost=1.0,
        lower_bound=0.8,
        upper_bound=1.2,
        currency="USD",
        decision=ForecastDecision.APPROVED,
        decided_at=_NOW,
        decided_by="op-1",
        ceiling_amount=ceiling_amount,
        gated_work_item=gated_work_item,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _redispatcher() -> tuple[
    ForecastGateRedispatcher, StubWorkPipeline, set[asyncio.Task[None]]
]:
    gate = StubWorkPipeline()
    tracked: set[asyncio.Task[None]] = set()
    return (
        ForecastGateRedispatcher(gate=gate, background_tasks=tracked),
        gate,
        tracked,
    )


async def _settle(tracked: set[asyncio.Task[None]]) -> None:
    """Wait for every spawned run, so assertions read a finished state."""
    if tracked:
        await asyncio.gather(*tuple(tracked))


async def test_approved_forecast_runs_the_work_it_held() -> None:
    redispatcher, gate, tracked = _redispatcher()
    forecast = _forecast(gated_work_item=_WORK_ITEM)

    await redispatcher.dispatch(forecast)
    await _settle(tracked)

    assert [item.title for item in gate.calls] == ["Ship the game"]


async def test_the_rebuilt_item_names_the_forecast_that_released_it() -> None:
    """The gate must resolve the approved row, not mint a second one."""
    redispatcher, gate, tracked = _redispatcher()
    forecast = _forecast(gated_work_item=_WORK_ITEM)

    await redispatcher.dispatch(forecast)
    await _settle(tracked)

    assert gate.calls[0].forecast_id == forecast.forecast_id
    assert gate.calls[0].correlation_id == "corr-001"


async def test_a_forecast_holding_no_work_dispatches_nothing() -> None:
    redispatcher, gate, tracked = _redispatcher()

    await redispatcher.dispatch(_forecast(gated_work_item=None))
    await _settle(tracked)

    assert gate.calls == []


async def test_an_unparseable_stored_item_is_refused_not_dropped() -> None:
    """Silence here would recreate the dead-end one layer down.

    Raised as a domain error rather than the underlying validation failure:
    the port and the approving service both promise one, and a third-party
    exception crossing that boundary is not something a caller can handle.
    """
    redispatcher, gate, _ = _redispatcher()
    forecast = _forecast(gated_work_item={"title": "no other fields"})

    with pytest.raises(ServiceUnavailableError):
        await redispatcher.dispatch(forecast)

    assert gate.calls == []


async def test_the_spawned_run_is_tracked_until_it_finishes() -> None:
    """An untracked task can be garbage-collected mid-run."""
    redispatcher, _, tracked = _redispatcher()

    await redispatcher.dispatch(_forecast(gated_work_item=_WORK_ITEM))

    assert len(tracked) == 1
    await _settle(tracked)
    assert tracked == set()
