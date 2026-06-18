"""Unit tests for the promotion cycle scheduler."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from synthorg.hr.promotion.cycle_scheduler import PromotionCycleScheduler
from synthorg.hr.promotion.service import PromotionService
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

pytestmark = pytest.mark.unit


def _paused_resolver() -> AsyncMock:
    """Build a spec'd resolver whose ``get_bool`` reports paused=True.

    Returns:
        An ``AsyncMock`` standing in for the config resolver.
    """
    resolver = AsyncMock(spec=ConfigResolverProtocol)
    resolver.get_bool.return_value = True
    return resolver


def _service_running_cycles(event: asyncio.Event) -> AsyncMock:
    """Build a spec'd promotion-service mock that signals each cycle.

    Returns:
        An ``AsyncMock`` whose ``run_cycle`` sets *event* and returns ().
    """
    service = AsyncMock(spec=PromotionService)

    async def _run_cycle() -> tuple[object, ...]:
        event.set()
        return ()

    service.run_cycle.side_effect = _run_cycle
    return service


async def test_interval_is_floored() -> None:
    """An interval below the floor is raised to the 60s minimum."""
    service = AsyncMock(spec=PromotionService)
    scheduler = PromotionCycleScheduler(service, interval_seconds=5.0)
    assert scheduler._interval == 60.0


async def test_start_runs_one_cycle_then_stop() -> None:
    """Starting the scheduler runs a cycle immediately; stop drains cleanly."""
    ran = asyncio.Event()
    service = _service_running_cycles(ran)
    scheduler = PromotionCycleScheduler(service, interval_seconds=60.0)

    await scheduler.start()
    await asyncio.wait_for(ran.wait(), timeout=5.0)
    await scheduler.stop()

    service.run_cycle.assert_awaited()


async def test_paused_setting_skips_cycle() -> None:
    """When the kill-switch is paused the loop never runs a cycle."""
    ran = asyncio.Event()
    service = _service_running_cycles(ran)
    scheduler = PromotionCycleScheduler(
        service,
        interval_seconds=60.0,
        config_resolver=_paused_resolver(),
    )

    await scheduler.start()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(ran.wait(), timeout=0.5)
    await scheduler.stop()

    service.run_cycle.assert_not_awaited()


async def test_stop_before_start_is_noop() -> None:
    """Stopping a never-started scheduler is harmless."""
    service = AsyncMock(spec=PromotionService)
    scheduler = PromotionCycleScheduler(service, interval_seconds=60.0)

    await scheduler.stop()

    service.run_cycle.assert_not_awaited()
