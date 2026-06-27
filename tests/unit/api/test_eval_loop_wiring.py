"""Unit tests for ``wire_eval_loop`` startup wiring.

Covers the dependency-absent skip (no tracker / training service),
idempotency for a re-entered lifespan, the default opt-out path (the
coordinator is published but the cycle scheduler stays dormant), and the
opt-in path that starts the scheduler when ``hr.eval_loop_cycle_enabled``
is set.
"""

import pytest

from synthorg.api.lifecycle_helpers.eval_loop_wiring import wire_eval_loop
from synthorg.api.state import AppState
from synthorg.hr.evaluation.loop_coordinator import EvalLoopCoordinator
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.state import HrStateSlice
from synthorg.hr.training.service import TrainingService
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


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


async def test_publishes_coordinator_with_scheduler_dormant_by_default() -> None:
    app_state = _wired_app_state()
    await wire_eval_loop(app_state)
    published = app_state.slice(HrStateSlice)
    # wire_eval_loop always publishes the coordinator when its deps exist ...
    assert published.eval_loop_coordinator is not None
    # ... but the unattended cycle driver is opt-in, so it stays dormant.
    assert published.eval_loop_cycle_scheduler is None


async def test_publishes_scheduler_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYNTHORG_HR_EVAL_LOOP_CYCLE_ENABLED", "true")
    app_state = _wired_app_state()
    await wire_eval_loop(app_state)
    published = app_state.slice(HrStateSlice)
    assert published.eval_loop_coordinator is not None
    scheduler = published.eval_loop_cycle_scheduler
    assert scheduler is not None
    # The scheduler is a live background task; stop it so the test loop
    # does not leak it.
    await scheduler.stop()
