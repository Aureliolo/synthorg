"""Tests for the feature service-hook lifecycle dispatcher.

Covers :class:`FeatureLifecycleRunner`: start order, reverse-order teardown
of only the started hooks, best-effort vs fatal start-failure handling, the
fatal-start rollback, and the per-hook stop budget (a hanging stop is logged
and abandoned, never wedging shutdown).
"""

import asyncio

import pytest

from synthorg._core.features import FeatureManifest
from synthorg.api.lifecycle_helpers.feature_lifecycle import (
    FeatureLifecycleRunner,
    build_feature_lifecycle_runner,
)

pytestmark = pytest.mark.unit


class _RecordingHook:
    """A ServiceLifecycleHook double recording start/stop into a shared log."""

    def __init__(  # noqa: PLR0913 -- test double exposes one flag per behaviour
        self,
        name: str,
        log: list[str],
        *,
        fatal_on_start_error: bool = False,
        raise_on_start: bool = False,
        hang_on_stop: bool = False,
        stop_timeout_seconds: float | None = None,
    ) -> None:
        self._name = name
        self._log = log
        self._fatal = fatal_on_start_error
        self._raise_on_start = raise_on_start
        self._hang_on_stop = hang_on_stop
        self._stop_timeout = stop_timeout_seconds

    @property
    def name(self) -> str:
        return self._name

    @property
    def start_timeout_seconds(self) -> float | None:
        return None

    @property
    def stop_timeout_seconds(self) -> float | None:
        return self._stop_timeout

    @property
    def fatal_on_start_error(self) -> bool:
        return self._fatal

    async def start(self) -> None:
        if self._raise_on_start:
            msg = f"{self._name} start boom"
            raise RuntimeError(msg)
        self._log.append(f"start:{self._name}")

    async def stop(self) -> None:
        if self._hang_on_stop:
            await asyncio.Event().wait()  # never resolves; relies on stop budget
        self._log.append(f"stop:{self._name}")


async def test_start_in_order_stop_in_reverse() -> None:
    log: list[str] = []
    runner = FeatureLifecycleRunner(
        [_RecordingHook("a", log), _RecordingHook("b", log), _RecordingHook("c", log)]
    )
    await runner.start_all()
    await runner.stop_all()
    assert log == [
        "start:a",
        "start:b",
        "start:c",
        "stop:c",
        "stop:b",
        "stop:a",
    ]


async def test_non_fatal_start_failure_continues_and_is_not_stopped() -> None:
    log: list[str] = []
    runner = FeatureLifecycleRunner(
        [
            _RecordingHook("a", log),
            _RecordingHook("b", log, raise_on_start=True),
            _RecordingHook("c", log),
        ]
    )
    await runner.start_all()
    await runner.stop_all()
    # b failed to start: never started, never stopped; a and c are fine.
    assert log == ["start:a", "start:c", "stop:c", "stop:a"]


async def test_fatal_start_failure_rolls_back_and_reraises() -> None:
    log: list[str] = []
    runner = FeatureLifecycleRunner(
        [
            _RecordingHook("a", log),
            _RecordingHook("b", log, raise_on_start=True, fatal_on_start_error=True),
            _RecordingHook("c", log),
        ]
    )
    with pytest.raises(RuntimeError, match="b start boom"):
        await runner.start_all()
    # a started then was rolled back; b/c never started; c never reached.
    assert log == ["start:a", "stop:a"]


async def test_hanging_stop_is_abandoned_at_budget() -> None:
    log: list[str] = []
    runner = FeatureLifecycleRunner(
        [
            _RecordingHook("a", log),
            _RecordingHook("b", log, hang_on_stop=True, stop_timeout_seconds=0.01),
        ]
    )
    await runner.start_all()
    # b's stop hangs but is abandoned at its 0.01s budget; a still stops.
    await runner.stop_all()
    assert log == ["start:a", "start:b", "stop:a"]


# ``build_feature_lifecycle_runner`` is the collector that makes the
# ``lifecycle_hooks`` manifest slot reachable from the composition root. These
# guard the regression where ``FeatureLifecycleRunner`` existed but no runner
# was ever built, so a feature's ``lifecycle_hooks`` were silently dropped at
# boot (the slot had no collector, unlike ``controllers`` / ``mcp_handlers`` /
# ``construction_wirer``).


async def test_build_runner_flattens_hooks_in_feature_dependency_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log: list[str] = []
    monkeypatch.setattr(
        "synthorg.api.lifecycle_helpers.feature_lifecycle.discover_features",
        lambda: (
            FeatureManifest(name="a", lifecycle_hooks=(_RecordingHook("a", log),)),
            FeatureManifest(name="b", lifecycle_hooks=(_RecordingHook("b", log),)),
        ),
    )
    runner = build_feature_lifecycle_runner()
    await runner.start_all()
    await runner.stop_all()
    # Discovery returns dependency-ordered manifests; the collector flattens in
    # that order, so start follows it and stop reverses it.
    assert log == ["start:a", "start:b", "stop:b", "stop:a"]


async def test_build_runner_no_hooks_when_no_feature_declares_any(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "synthorg.api.lifecycle_helpers.feature_lifecycle.discover_features",
        lambda: (FeatureManifest(name="a"), FeatureManifest(name="b")),
    )
    runner = build_feature_lifecycle_runner()
    # Mirrors today's real state: every feature declares ``lifecycle_hooks=()``,
    # so the runner is empty but still constructed and wired (no-op, no error).
    await runner.start_all()
    await runner.stop_all()
