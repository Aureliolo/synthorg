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
from synthorg.settings.resolver import ConfigResolver
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


async def test_scheduler_unrestartable_after_failed_stop() -> None:
    """A scheduler marked unrestartable by a timed-out stop refuses start."""
    service = mock_of[ToolsmithService](run_cycle=AsyncMock(return_value=()))
    scheduler = ToolsmithCycleScheduler(service, interval_seconds=_INTERVAL_SECONDS)
    scheduler._stop_failed = True

    with pytest.raises(RuntimeError, match="unrestartable"):
        await scheduler.start()

    assert scheduler._task is None
    service.run_cycle.assert_not_awaited()


async def test_scheduler_paused_by_kill_switch_skips_cycle() -> None:
    """When the resolver reports paused, the loop never runs a cycle."""
    checked = asyncio.Event()

    async def _get_bool(namespace: str, key: str) -> bool:
        del namespace, key
        checked.set()
        return True

    resolver = mock_of[ConfigResolver](get_bool=AsyncMock(side_effect=_get_bool))
    service = mock_of[ToolsmithService](run_cycle=AsyncMock(return_value=()))
    scheduler = ToolsmithCycleScheduler(
        service,
        interval_seconds=_INTERVAL_SECONDS,
        config_resolver=resolver,
    )

    await scheduler.start()
    await asyncio.wait_for(checked.wait(), timeout=_TIMEOUT_SECONDS)
    await scheduler.stop()

    resolver.get_bool.assert_awaited()
    service.run_cycle.assert_not_awaited()
