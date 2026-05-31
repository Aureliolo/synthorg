# module-kind: tests
"""Tests for the scaling benchmark signal source (#1983).

Closes the benchmark -> hiring/scaling feedback path: the source reads the same
golden-benchmark learning curve the meta-loop consumes and emits a regression
signal the ``PerformancePruningStrategy`` acts on (defer pruning on a measured
quality drop).
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from synthorg.hr.scaling.models import ScalingSignal
from synthorg.hr.scaling.signals.benchmark import (
    BENCHMARK_REGRESSION_SIGNAL,
    BENCHMARK_TREND_SIGNAL,
    BenchmarkSignalSource,
)
from synthorg.meta.learning_curve import ScorecardSummary, append_summary

pytestmark = pytest.mark.unit

_AGENT_IDS: Final[tuple[str, ...]] = ()


def _summary(label: str, total: int, *, hour: int) -> ScorecardSummary:
    return ScorecardSummary(
        run_label=label,
        generated_at=datetime(2026, 4, 11, hour, 0, 0, tzinfo=UTC),
        total=total,
        max_total=100,
        is_passing=total >= 50,
    )


def _signal_value(signals: tuple[ScalingSignal, ...], name: str) -> float:
    return next(s.value for s in signals if s.name == name)


async def test_no_history_dir_is_neutral() -> None:
    """No configured history dir yields neutral, non-regression signals."""
    source = BenchmarkSignalSource(history_dir=None)
    signals = await source.collect(_AGENT_IDS)
    assert _signal_value(signals, BENCHMARK_REGRESSION_SIGNAL) == 0.0
    assert _signal_value(signals, BENCHMARK_TREND_SIGNAL) == 0.0


async def test_empty_history_dir_is_neutral(tmp_path: Path) -> None:
    """An empty history directory (no runs yet) yields neutral signals."""
    source = BenchmarkSignalSource(history_dir=tmp_path)
    signals = await source.collect(_AGENT_IDS)
    assert _signal_value(signals, BENCHMARK_REGRESSION_SIGNAL) == 0.0


async def test_regressing_curve_sets_regression_signal(tmp_path: Path) -> None:
    """A latest run that backslid past the threshold flags a regression."""
    append_summary(tmp_path, _summary("run-1", total=90, hour=1))
    append_summary(tmp_path, _summary("run-2", total=40, hour=2))
    source = BenchmarkSignalSource(history_dir=tmp_path)

    signals = await source.collect(_AGENT_IDS)

    assert _signal_value(signals, BENCHMARK_REGRESSION_SIGNAL) == 1.0
    assert _signal_value(signals, BENCHMARK_TREND_SIGNAL) == -50.0


async def test_rising_curve_does_not_flag_regression(tmp_path: Path) -> None:
    """A rising curve emits a positive trend and no regression."""
    append_summary(tmp_path, _summary("run-1", total=40, hour=1))
    append_summary(tmp_path, _summary("run-2", total=90, hour=2))
    source = BenchmarkSignalSource(history_dir=tmp_path)

    signals = await source.collect(_AGENT_IDS)

    assert _signal_value(signals, BENCHMARK_REGRESSION_SIGNAL) == 0.0
    assert _signal_value(signals, BENCHMARK_TREND_SIGNAL) == 50.0


async def test_source_name() -> None:
    assert BenchmarkSignalSource().name == "benchmark"
