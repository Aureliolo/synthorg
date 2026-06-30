"""Unit tests for the eval-loop cycle scheduler's live gates + re-reads.

The scheduler is ghost-wired (always constructed + started) and gates work per
tick on ``hr.eval_loop_cycle_enabled`` AND ``hr.eval_loop_cycle_paused``; its
cadence and look-back window are re-read live each tick. These tests drive the
protected hooks directly (no background loop) so the gate / re-read behaviour is
asserted deterministically.
"""

from datetime import timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.evaluation.cycle_scheduler import EvalLoopCycleScheduler
from synthorg.hr.evaluation.loop_coordinator import EvalLoopCoordinator
from synthorg.settings.resolver import ConfigResolver
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _coordinator() -> AsyncMock:
    coordinator = AsyncMock(spec=EvalLoopCoordinator)
    coordinator.run_cycle.return_value = SimpleNamespace(
        cycle_id=NotBlankStr("cycle-1"),
        agents_evaluated=0,
        training_triggered=False,
    )
    return coordinator


def _scheduler(
    *,
    resolver: ConfigResolver | None,
    coordinator: AsyncMock | None = None,
    interval_seconds: float = 86400.0,
    window: timedelta = timedelta(hours=168),
) -> EvalLoopCycleScheduler:
    return EvalLoopCycleScheduler(
        coordinator or _coordinator(),
        interval_seconds=interval_seconds,
        window=window,
        config_resolver=resolver,
    )


def _bool_resolver(values: dict[str, bool]) -> ConfigResolver:
    async def _get_bool(namespace: str, key: str) -> bool:
        del namespace
        return values[key]

    return cast("ConfigResolver", mock_of[ConfigResolver](get_bool=_get_bool))


class TestCycleEnabledGate:
    """``_resolve_cycle_enabled`` folds the master switch + pause flag."""

    async def test_disabled_when_no_resolver(self) -> None:
        # Opt-in loop: a resolver outage fails safe to disabled, never starts.
        assert await _scheduler(resolver=None)._resolve_cycle_enabled() is False

    async def test_enabled_and_unpaused_runs(self) -> None:
        resolver = _bool_resolver(
            {"eval_loop_cycle_enabled": True, "eval_loop_cycle_paused": False}
        )
        assert await _scheduler(resolver=resolver)._resolve_cycle_enabled() is True

    async def test_enabled_but_paused_skips(self) -> None:
        resolver = _bool_resolver(
            {"eval_loop_cycle_enabled": True, "eval_loop_cycle_paused": True}
        )
        assert await _scheduler(resolver=resolver)._resolve_cycle_enabled() is False

    async def test_disabled_short_circuits_pause_read(self) -> None:
        # When the master switch is off the pause flag is never consulted.
        resolver = _bool_resolver({"eval_loop_cycle_enabled": False})
        assert await _scheduler(resolver=resolver)._resolve_cycle_enabled() is False


class TestLiveCadenceAndWindow:
    """Interval + window are re-read per tick, applying with no restart."""

    async def test_interval_re_read_from_resolver(self) -> None:
        resolver = mock_of[ConfigResolver](get_float=AsyncMock(return_value=120.0))
        scheduler = _scheduler(resolver=resolver, interval_seconds=86400.0)
        assert await scheduler._resolve_wait_interval() == 120.0

    async def test_interval_falls_back_without_resolver(self) -> None:
        scheduler = _scheduler(resolver=None, interval_seconds=300.0)
        assert await scheduler._resolve_wait_interval() == 300.0

    async def test_window_re_read_and_passed_to_run_cycle(self) -> None:
        coordinator = _coordinator()
        resolver = mock_of[ConfigResolver](get_float=AsyncMock(return_value=2.0))
        scheduler = _scheduler(
            resolver=resolver,
            coordinator=coordinator,
            window=timedelta(hours=168),
        )
        await scheduler._run_cycle_once()
        coordinator.run_cycle.assert_awaited_once_with(window=timedelta(hours=2))

    async def test_window_falls_back_to_construction_without_resolver(self) -> None:
        # Without a resolver the cycle uses the construction-time window, so a
        # resolver outage cannot widen or narrow the look-back unexpectedly.
        coordinator = _coordinator()
        scheduler = _scheduler(
            resolver=None,
            coordinator=coordinator,
            window=timedelta(hours=72),
        )
        await scheduler._run_cycle_once()
        coordinator.run_cycle.assert_awaited_once_with(window=timedelta(hours=72))

    @pytest.mark.parametrize(
        "bad_hours",
        [0.0, -1.0, float("nan"), float("inf"), 1e15],
        ids=["zero", "negative", "nan", "inf", "overflow"],
    )
    async def test_invalid_window_collapses_to_construction(
        self, bad_hours: float
    ) -> None:
        # A stored nan / inf / non-positive / finite-but-enormous window only
        # fails over on a resolver error otherwise; the runtime path must
        # collapse it to the last-known-good window rather than build a
        # nonsensical timedelta or raise OverflowError past timedelta.max.
        coordinator = _coordinator()
        resolver = mock_of[ConfigResolver](get_float=AsyncMock(return_value=bad_hours))
        scheduler = _scheduler(
            resolver=resolver,
            coordinator=coordinator,
            window=timedelta(hours=72),
        )
        await scheduler._run_cycle_once()
        coordinator.run_cycle.assert_awaited_once_with(window=timedelta(hours=72))
