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
        # Deterministically wait for one tick (the per-tick mode read) and
        # assert the cycle was skipped, rather than busy-polling sleep(0).
        ticked = asyncio.Event()

        async def _get_str(*_a: object, **_k: object) -> str:
            ticked.set()
            return "off"

        service = mock_of[ModelRefreshService](run_cycle=AsyncMock())
        resolver = mock_of[ConfigResolver](
            get_str=AsyncMock(side_effect=_get_str),
            get_bool=AsyncMock(return_value=False),
        )
        scheduler = ModelRefreshScheduler(
            service,
            interval_seconds=60.0,
            config_resolver=resolver,
        )
        await scheduler.start()
        try:
            await asyncio.wait_for(ticked.wait(), timeout=5.0)
        finally:
            await scheduler.stop()
        service.run_cycle.assert_not_called()

    async def test_passes_auto_apply_flag_when_enabled(self) -> None:
        ran = asyncio.Event()
        captured: dict[str, object] = {}

        async def _run_cycle(**kwargs: object) -> RefreshCycleReport:
            captured.update(kwargs)
            ran.set()
            return RefreshCycleReport()

        service = mock_of[ModelRefreshService](
            run_cycle=AsyncMock(side_effect=_run_cycle)
        )
        resolver = mock_of[ConfigResolver](
            get_str=AsyncMock(return_value="reconcile_recommend"),
            get_bool=AsyncMock(return_value=True),
        )
        scheduler = ModelRefreshScheduler(
            service,
            interval_seconds=60.0,
            config_resolver=resolver,
        )
        await scheduler.start()
        try:
            await asyncio.wait_for(ran.wait(), timeout=5.0)
        finally:
            await scheduler.stop()
        assert captured["auto_apply"] is True

    async def test_stop_drain_timeout_marks_unrestartable(self) -> None:
        started = asyncio.Event()
        release_cleanup = asyncio.Event()

        async def _run_cycle(**_kwargs: object) -> RefreshCycleReport:
            started.set()
            try:
                await asyncio.Event().wait()  # parked until stop() cancels the task
            finally:
                # Cancellation-time cleanup that deterministically outlasts the
                # drain deadline (gated on an Event the test only sets after it
                # has asserted the hard-timeout branch), so stop() is forced
                # onto its hard-timeout branch without a wall-clock race.
                await release_cleanup.wait()
            return RefreshCycleReport()

        service = mock_of[ModelRefreshService](
            run_cycle=AsyncMock(side_effect=_run_cycle)
        )
        scheduler = ModelRefreshScheduler(
            service,
            interval_seconds=60.0,
            config_resolver=_resolver("detect_only"),
        )
        scheduler._drain_timeout = 0.05
        await scheduler.start()
        await asyncio.wait_for(started.wait(), timeout=5.0)
        try:
            with pytest.raises(TimeoutError):
                await scheduler.stop()
        finally:
            # Always release the parked cancellation cleanup so a failed
            # assertion cannot leave the orphaned cycle task blocked.
            release_cleanup.set()
        with pytest.raises(RuntimeError, match="unrestartable"):
            await scheduler.start()

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
