"""Unit tests for ``wire_eval_loop`` startup wiring.

Covers the dependency-absent skip (no tracker / training service),
idempotency for a re-entered lifespan, and the ghost-wire path: the cycle
scheduler is always constructed and started, then gated per tick on
``hr.eval_loop_cycle_enabled`` (so toggling it needs no restart).
"""

import pytest

from synthorg.api.lifecycle_helpers.eval_loop_wiring import (
    _select_provider,
    wire_eval_loop,
)
from synthorg.api.state import AppState
from synthorg.hr.evaluation.loop_coordinator import EvalLoopCoordinator
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.state import HrStateSlice
from synthorg.hr.training.service import TrainingService
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.registry import ProviderRegistry
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


class TestSelectProvider:
    """``_select_provider`` resolution + misconfiguration handling."""

    def test_pinned_present_returns_it(self) -> None:
        driver = mock_of[BaseCompletionProvider]()
        registry = ProviderRegistry({"example-provider": driver})
        assert _select_provider(registry, "example-provider") is driver

    def test_pinned_but_absent_returns_none(self) -> None:
        # An explicit-but-absent provider (typo / stale config) degrades to
        # the deterministic strategy (None), never silently substitutes a
        # different provider than the operator named.
        registry = ProviderRegistry(
            {"example-provider": mock_of[BaseCompletionProvider]()}
        )
        assert _select_provider(registry, "absent-name") is None

    def test_unpinned_uses_first_available(self) -> None:
        driver = mock_of[BaseCompletionProvider]()
        registry = ProviderRegistry({"example-provider": driver})
        assert _select_provider(registry, "") is driver

    def test_no_registry_returns_none(self) -> None:
        assert _select_provider(None, "example-provider") is None


def _wired_app_state() -> AppState:
    """App state with a tracker + training service wired, ready for eval loop."""
    return make_app_state(
        slices={
            HrStateSlice: {
                "performance_tracker": PerformanceTracker(),
                "training_service": mock_of[TrainingService](),
                "eval_loop_coordinator": None,
                "eval_loop_cycle_scheduler": None,
            },
        },
    )


async def test_skips_when_tracker_or_training_absent() -> None:
    app_state = make_app_state(
        slices={
            HrStateSlice: {
                "performance_tracker": None,
                "training_service": None,
                "eval_loop_coordinator": None,
            },
        },
    )
    await wire_eval_loop(app_state)
    assert app_state.slice(HrStateSlice).eval_loop_coordinator is None


async def test_already_wired_is_idempotent() -> None:
    existing = mock_of[EvalLoopCoordinator]()
    app_state = make_app_state(
        slices={
            HrStateSlice: {
                "performance_tracker": PerformanceTracker(),
                "training_service": mock_of[TrainingService](),
                "eval_loop_coordinator": existing,
            },
        },
    )
    await wire_eval_loop(app_state)
    assert app_state.slice(HrStateSlice).eval_loop_coordinator is existing


async def test_ghost_wires_and_starts_scheduler_by_default() -> None:
    """The scheduler is always constructed + started, even with the switch off.

    The default ``hr.eval_loop_cycle_enabled=false`` no longer gates wiring:
    the loop runs but idles per tick (gated by the resolver), so toggling the
    switch takes effect with no restart.
    """
    app_state = _wired_app_state()
    await wire_eval_loop(app_state)
    published = app_state.slice(HrStateSlice)
    scheduler = published.eval_loop_cycle_scheduler
    try:
        assert published.eval_loop_coordinator is not None
        assert scheduler is not None
        # Not just published: the loop must actually be spinning, so a no-op
        # ``start()`` regression (returns without scheduling the task) fails.
        assert scheduler.is_running
    finally:
        # The scheduler is a live background task; stop it so the test loop
        # does not leak it even if an assertion above fails.
        if scheduler is not None:
            await scheduler.stop()
