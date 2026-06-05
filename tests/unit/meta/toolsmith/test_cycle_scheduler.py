"""Lifecycle tests for the toolsmith autonomous-cycle scheduler.

Seam C of the autonomous detection loop: the periodic scheduler
drives ``ToolsmithService.run_cycle`` on a cadence so the org proposes new
tools without a manual trigger. These tests pin that the loop runs a cycle,
stops cleanly, no-ops when never started, and rejects a sub-minute cadence.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from synthorg.meta.toolsmith.cycle_scheduler import ToolsmithCycleScheduler
from synthorg.meta.toolsmith.service import ToolsmithService
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_TIMEOUT_SECONDS = 5.0
_INTERVAL_SECONDS = 60.0
_SUB_MINUTE_SECONDS = 30.0


async def test_scheduler_runs_cycle_then_stops_cleanly() -> None:
    ran = asyncio.Event()

    async def _run_cycle(*, now: object = None) -> tuple[()]:
        del now
        ran.set()
        return ()

    service = mock_of[ToolsmithService](run_cycle=AsyncMock(side_effect=_run_cycle))
    scheduler = ToolsmithCycleScheduler(service, interval_seconds=_INTERVAL_SECONDS)

    await scheduler.start()
    await asyncio.wait_for(ran.wait(), timeout=_TIMEOUT_SECONDS)
    await scheduler.stop()

    service.run_cycle.assert_awaited()
    assert scheduler._task is None


async def test_scheduler_stop_without_start_is_noop() -> None:
    service = mock_of[ToolsmithService](run_cycle=AsyncMock(return_value=()))
    scheduler = ToolsmithCycleScheduler(service, interval_seconds=_INTERVAL_SECONDS)

    await scheduler.stop()


def test_scheduler_rejects_sub_minute_interval() -> None:
    service = mock_of[ToolsmithService](run_cycle=AsyncMock(return_value=()))

    with pytest.raises(ValueError, match="interval_seconds"):
        ToolsmithCycleScheduler(service, interval_seconds=_SUB_MINUTE_SECONDS)
