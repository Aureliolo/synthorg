"""Unit tests for the automatic promotion cycle scan."""

from datetime import UTC, datetime

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.agent import AgentIdentity
from synthorg.core.task_enums import Complexity, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import TaskMetricRecord
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.promotion.config import (
    PromotionApprovalConfig,
    PromotionConfig,
)
from synthorg.hr.promotion.cycle import run_promotion_cycle
from synthorg.hr.promotion.factory import build_promotion_service
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel

from .conftest import make_agent_identity

pytestmark = pytest.mark.unit


async def _seed_metrics(
    tracker: PerformanceTracker,
    agent_id: str,
    *,
    count: int = 15,
    quality: float = 8.0,
    is_success: bool = True,
) -> None:
    """Record several task metrics for an agent."""
    for i in range(count):
        await tracker.record_task_metric(
            TaskMetricRecord(
                agent_id=NotBlankStr(agent_id),
                task_id=NotBlankStr(f"task-{i:03d}"),
                task_type=TaskType.DEVELOPMENT,
                completed_at=datetime.now(UTC),
                is_success=is_success,
                duration_seconds=60.0,
                cost=0.01,
                currency="EUR",
                turns_used=5,
                tokens_used=1000,
                quality_score=quality,
                complexity=Complexity.MEDIUM,
            )
        )


async def test_applies_auto_approved_promotion(
    registry: AgentRegistryService,
    tracker: PerformanceTracker,
) -> None:
    """A Junior agent with strong metrics is promoted in one cycle."""
    identity = make_agent_identity(name="promotable", level=SeniorityLevel.JUNIOR)
    await registry.register(identity)
    await _seed_metrics(tracker, str(identity.id), quality=8.0)
    service = build_promotion_service(registry=registry, tracker=tracker)

    applied = await run_promotion_cycle(service)

    assert len(applied) == 1
    assert applied[0].old_level == SeniorityLevel.JUNIOR
    assert applied[0].new_level == SeniorityLevel.MID
    refreshed = await registry.get(NotBlankStr(str(identity.id)))
    assert refreshed is not None
    assert refreshed.level == SeniorityLevel.MID


async def test_disabled_config_skips_scan(
    registry: AgentRegistryService,
    tracker: PerformanceTracker,
) -> None:
    """A disabled subsystem applies nothing and never touches the registry."""
    identity = make_agent_identity(name="promotable", level=SeniorityLevel.JUNIOR)
    await registry.register(identity)
    await _seed_metrics(tracker, str(identity.id), quality=8.0)
    service = build_promotion_service(
        registry=registry,
        tracker=tracker,
        config=PromotionConfig(enabled=False),
    )

    applied = await run_promotion_cycle(service)

    assert applied == ()
    refreshed = await registry.get(NotBlankStr(str(identity.id)))
    assert refreshed is not None
    assert refreshed.level == SeniorityLevel.JUNIOR


async def test_cooldown_blocks_second_change(
    registry: AgentRegistryService,
    tracker: PerformanceTracker,
) -> None:
    """An agent promoted this cycle is skipped while in cooldown."""
    identity = make_agent_identity(name="promotable", level=SeniorityLevel.JUNIOR)
    await registry.register(identity)
    await _seed_metrics(tracker, str(identity.id), quality=8.0)
    service = build_promotion_service(registry=registry, tracker=tracker)

    first = await run_promotion_cycle(service)
    second = await run_promotion_cycle(service)

    assert len(first) == 1
    assert second == ()


async def test_human_gated_change_is_not_applied(
    registry: AgentRegistryService,
    tracker: PerformanceTracker,
) -> None:
    """A change needing human approval creates an approval, applies nothing."""
    identity = make_agent_identity(name="promotable", level=SeniorityLevel.JUNIOR)
    await registry.register(identity)
    await _seed_metrics(tracker, str(identity.id), quality=8.0)
    approval_store = ApprovalStore()
    service = build_promotion_service(
        registry=registry,
        tracker=tracker,
        config=PromotionConfig(
            approval=PromotionApprovalConfig(
                human_approval_from_level=SeniorityLevel.MID,
            ),
        ),
        approval_store=approval_store,
    )

    applied = await run_promotion_cycle(service)

    assert applied == ()
    refreshed = await registry.get(NotBlankStr(str(identity.id)))
    assert refreshed is not None
    assert refreshed.level == SeniorityLevel.JUNIOR
    pending = await approval_store.list_items()
    assert len(pending) == 1


async def test_empty_registry_returns_nothing(
    registry: AgentRegistryService,
    tracker: PerformanceTracker,
) -> None:
    """A scan over no agents applies nothing."""
    service = build_promotion_service(registry=registry, tracker=tracker)

    assert await run_promotion_cycle(service) == ()


async def test_cycle_does_not_refetch_identity_per_evaluation(
    registry: AgentRegistryService,
    tracker: PerformanceTracker,
) -> None:
    """The sweep threads the ``list_active`` identity into evaluate/request.

    Only the authoritative under-lock read in ``_apply_level_change`` should
    hit ``registry.get`` (once for the applied agent); the evaluation and
    request steps must reuse the pre-loaded identity rather than re-fetching.
    """
    identity = make_agent_identity(name="promotable", level=SeniorityLevel.JUNIOR)
    await registry.register(identity)
    await _seed_metrics(tracker, str(identity.id), quality=8.0)
    service = build_promotion_service(registry=registry, tracker=tracker)

    get_calls = 0
    original_get = registry.get

    async def _counting_get(agent_id: NotBlankStr) -> AgentIdentity | None:
        nonlocal get_calls
        get_calls += 1
        return await original_get(agent_id)

    registry.get = _counting_get  # type: ignore[method-assign]
    try:
        applied = await run_promotion_cycle(service)
    finally:
        registry.get = original_get  # type: ignore[method-assign]

    assert len(applied) == 1
    # Pre-fix this was 3 (evaluate + request + apply); now only the
    # under-lock apply read remains.
    assert get_calls == 1
