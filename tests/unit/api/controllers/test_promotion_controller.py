"""Unit tests for the promotion controller.

Calls the handlers directly with a fake ``State`` (the
``test_agent_roster`` pattern) so the evaluate / apply / history / cycle
logic -- and the 503 when the service is unwired -- is covered without a
full TestClient.
"""

from datetime import UTC, datetime

import pytest
from litestar.datastructures import State

from synthorg.api.controllers.promotion import PromotionController
from synthorg.api.cursor import CursorSecret
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.task_enums import Complexity, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import PromotionDirection
from synthorg.hr.performance.models import TaskMetricRecord
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.promotion.factory import build_promotion_service
from synthorg.hr.promotion.service import PromotionService
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
from synthorg.hr.state import HrStateSlice
from tests._shared import make_app_state
from tests.unit.hr.promotion.conftest import make_agent_identity

pytestmark = pytest.mark.unit


def _controller() -> PromotionController:
    """Build a route-free controller instance for direct handler calls."""
    return object.__new__(PromotionController)


async def _seeded_service() -> tuple[PromotionService, str]:
    """Build a service over a registry with one promotable Junior agent.

    Returns:
        The service and the seeded agent's id.
    """
    registry = AgentRegistryService()
    tracker = PerformanceTracker()
    identity = make_agent_identity(name="promotable", level=SeniorityLevel.JUNIOR)
    await registry.register(identity)
    agent_id = str(identity.id)
    for i in range(15):
        await tracker.record_task_metric(
            TaskMetricRecord(
                agent_id=NotBlankStr(agent_id),
                task_id=NotBlankStr(f"task-{i:03d}"),
                task_type=TaskType.DEVELOPMENT,
                completed_at=datetime.now(UTC),
                is_success=True,
                duration_seconds=60.0,
                cost=0.01,
                currency="EUR",
                turns_used=5,
                tokens_used=1000,
                quality_score=8.0,
                complexity=Complexity.MEDIUM,
            )
        )
    service = build_promotion_service(registry=registry, tracker=tracker)
    return service, agent_id


def _state_with(service: PromotionService | None) -> State:
    """Build a fake ``State`` whose HR slice holds *service*."""
    state = State()
    state.app_state = make_app_state(
        slices={HrStateSlice: {"promotion_service": service}},
        cursor_secret=CursorSecret.from_key("test-cursor-secret-key-0123456789"),
    )
    return state


async def test_evaluate_returns_eligible_evaluation() -> None:
    service, agent_id = await _seeded_service()
    result = await PromotionController.evaluate.fn(
        _controller(),
        state=_state_with(service),
        agent_id=agent_id,
        direction=PromotionDirection.PROMOTION,
    )
    assert result.data is not None
    assert result.data.eligible is True
    assert result.data.target_level == SeniorityLevel.MID.value


async def test_apply_auto_approves_and_applies() -> None:
    service, agent_id = await _seeded_service()
    result = await PromotionController.apply.fn(
        _controller(),
        state=_state_with(service),
        agent_id=agent_id,
        direction=PromotionDirection.PROMOTION,
    )
    assert result.data is not None
    assert result.data.applied is not None
    assert result.data.applied.new_level == SeniorityLevel.MID.value


async def test_history_reflects_applied_change() -> None:
    service, agent_id = await _seeded_service()
    state = _state_with(service)
    before = await PromotionController.history.fn(
        _controller(), state=state, agent_id=agent_id
    )
    assert before.data == ()
    await PromotionController.apply.fn(
        _controller(),
        state=state,
        agent_id=agent_id,
        direction=PromotionDirection.PROMOTION,
    )
    after = await PromotionController.history.fn(
        _controller(), state=state, agent_id=agent_id
    )
    assert after.data is not None
    assert len(after.data) == 1


async def test_trigger_cycle_applies_changes() -> None:
    service, _ = await _seeded_service()
    result = await PromotionController.trigger_cycle.fn(
        _controller(), state=_state_with(service)
    )
    assert result.data is not None
    assert len(result.data) == 1


async def test_unwired_service_raises_503() -> None:
    with pytest.raises(ServiceUnavailableError):
        await PromotionController.trigger_cycle.fn(
            _controller(), state=_state_with(None)
        )
