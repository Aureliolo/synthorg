"""Tests for the model-refresh background scheduler."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from synthorg.providers.management.model_refresh_service import (
    ModelRefreshService,
    RefreshCycleReport,
)
from synthorg.providers.management.refresh_scheduler import ModelRefreshScheduler
from synthorg.settings.resolver import ConfigResolver
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _resolver(mode: str) -> ConfigResolver:
    resolver: ConfigResolver = mock_of[ConfigResolver](
        get_str=AsyncMock(return_value=mode),
        get_bool=AsyncMock(return_value=False),
    )
    return resolver


class TestModelRefreshScheduler:
    def test_interval_floor_rejected(self) -> None:
        with pytest.raises(ValueError, match="interval_seconds"):
            ModelRefreshScheduler(
                mock_of[ModelRefreshService](),
                interval_seconds=59.0,
                config_resolver=_resolver("detect_only"),
            )

    async def test_runs_cycle_when_mode_scheduled(self) -> None:
        ran = asyncio.Event()

        async def _run_cycle(**_kwargs: object) -> RefreshCycleReport:
            ran.set()
            return RefreshCycleReport()

        service = mock_of[ModelRefreshService](
            run_cycle=AsyncMock(side_effect=_run_cycle)
        )
        scheduler = ModelRefreshScheduler(
            service,
            interval_seconds=60.0,
            config_resolver=_resolver("detect_only"),
        )
        await scheduler.start()
        try:
            await asyncio.wait_for(ran.wait(), timeout=5.0)
        finally:
            await scheduler.stop()
        service.run_cycle.assert_awaited()

    async def test_skips_cycle_when_off(self) -> None:
        service = mock_of[ModelRefreshService](run_cycle=AsyncMock())
        scheduler = ModelRefreshScheduler(
            service,
            interval_seconds=60.0,
            config_resolver=_resolver("off"),
        )
        await scheduler.start()
        try:
            for _ in range(5):
                await asyncio.sleep(0)
        finally:
            await scheduler.stop()
        service.run_cycle.assert_not_called()

    async def test_start_idempotent(self) -> None:
        service = mock_of[ModelRefreshService](
            run_cycle=AsyncMock(return_value=RefreshCycleReport()),
        )
        scheduler = ModelRefreshScheduler(
            service,
            interval_seconds=60.0,
            config_resolver=_resolver("off"),
        )
        await scheduler.start()
        await scheduler.start()  # second start is a no-op
        await scheduler.stop()

    async def test_stop_allows_restart(self) -> None:
        service = mock_of[ModelRefreshService](
            run_cycle=AsyncMock(return_value=RefreshCycleReport()),
        )
        scheduler = ModelRefreshScheduler(
            service,
            interval_seconds=60.0,
            config_resolver=_resolver("off"),
        )
        await scheduler.start()
        await scheduler.stop()
        await scheduler.start()
        await scheduler.stop()
