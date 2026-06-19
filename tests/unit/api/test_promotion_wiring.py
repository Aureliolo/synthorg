"""Unit tests for ``wire_promotion`` startup wiring.

Covers the disabled-config skip, idempotency for re-entered lifespans,
the dependency-absent skip (no registry / tracker), the happy path that
starts the scheduler before publishing the slice, and the rollback that
leaves the slice unpublished when ``scheduler.start()`` fails.
"""

import pytest

from synthorg.api.lifecycle_helpers.promotion_wiring import wire_promotion
from synthorg.api.state import AppState
from synthorg.approval.state import ApprovalStateSlice
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.promotion.config import PromotionConfig
from synthorg.hr.promotion.cycle_scheduler import PromotionCycleScheduler
from synthorg.hr.promotion.service import PromotionService
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.state import HrStateSlice
from synthorg.security.state import SecurityStateSlice
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _wired_app_state() -> AppState:
    """App state with a registry + tracker wired, ready for promotion."""
    return make_app_state(
        slices={
            HrStateSlice: {
                "agent_registry": AgentRegistryService(),
                "performance_tracker": PerformanceTracker(),
                "promotion_service": None,
                "promotion_cycle_scheduler": None,
            },
            ApprovalStateSlice: {"store": None},
            SecurityStateSlice: {"trust_service": None},
        },
    )


async def test_disabled_config_wires_nothing() -> None:
    app_state = _wired_app_state()
    await wire_promotion(app_state, config=PromotionConfig(enabled=False))
    assert app_state.slice(HrStateSlice).promotion_service is None


async def test_already_wired_is_idempotent() -> None:
    existing = mock_of[PromotionService]()
    app_state = make_app_state(
        slices={
            HrStateSlice: {
                "agent_registry": AgentRegistryService(),
                "performance_tracker": PerformanceTracker(),
                "promotion_service": existing,
                "promotion_cycle_scheduler": None,
            },
        },
    )
    await wire_promotion(app_state, config=PromotionConfig())
    assert app_state.slice(HrStateSlice).promotion_service is existing


async def test_skips_when_registry_or_tracker_absent() -> None:
    app_state = make_app_state(
        slices={
            HrStateSlice: {
                "agent_registry": None,
                "performance_tracker": None,
                "promotion_service": None,
                "promotion_cycle_scheduler": None,
            },
        },
    )
    await wire_promotion(app_state, config=PromotionConfig())
    assert app_state.slice(HrStateSlice).promotion_service is None


async def test_publishes_service_and_scheduler() -> None:
    app_state = _wired_app_state()
    await wire_promotion(app_state, config=PromotionConfig())
    published = app_state.slice(HrStateSlice)
    assert published.promotion_service is not None
    scheduler = published.promotion_cycle_scheduler
    assert scheduler is not None
    # The scheduler is a live background task; stop it so the test loop
    # does not leak it.
    await scheduler.stop()


async def test_rollback_when_scheduler_start_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _failing_start(_self: PromotionCycleScheduler) -> None:
        msg = "start boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(PromotionCycleScheduler, "start", _failing_start)
    app_state = _wired_app_state()
    await wire_promotion(app_state, config=PromotionConfig())
    # Start failed: the slice is never published, so the controller 503s
    # rather than presenting a half-wired subsystem.
    assert app_state.slice(HrStateSlice).promotion_service is None
    assert app_state.slice(HrStateSlice).promotion_cycle_scheduler is None
