"""Unit tests for PromotionService orchestrator."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.enums import (
    ApprovalStatus,
    Complexity,
    SeniorityLevel,
    TaskType,
)
from synthorg.core.types import NotBlankStr
from synthorg.hr.errors import (
    PromotionApprovalRequiredError,
    PromotionCooldownError,
    PromotionError,
)
from synthorg.hr.performance.models import TaskMetricRecord
from synthorg.hr.promotion.config import (
    PromotionConfig,
)
from synthorg.hr.promotion.seniority_approval_strategy import (
    SeniorityApprovalStrategy,
)
from synthorg.hr.promotion.seniority_model_mapping import (
    SeniorityModelMapping,
)
from synthorg.hr.promotion.service import PromotionService
from synthorg.hr.promotion.threshold_evaluator import ThresholdEvaluator

from .conftest import make_agent_identity

if TYPE_CHECKING:
    from synthorg.hr.performance.tracker import PerformanceTracker
    from synthorg.hr.registry import AgentRegistryService

# ── Helpers ──────────────────────────────────────────────────────


def _make_service(
    *,
    registry: AgentRegistryService,
    tracker: PerformanceTracker,
    config: PromotionConfig | None = None,
    approval_store: ApprovalStore | None = None,
) -> PromotionService:
    """Build a PromotionService with real strategy implementations."""
    cfg = config or PromotionConfig()
    return PromotionService(
        criteria_strategy=ThresholdEvaluator(config=cfg.criteria),
        approval_strategy=SeniorityApprovalStrategy(config=cfg.approval),
        model_mapping_strategy=SeniorityModelMapping(
            config=cfg.model_mapping,
        ),
        registry=registry,
        tracker=tracker,
        config=cfg,
        approval_store=approval_store,
    )


async def _seed_metrics(
    tracker: PerformanceTracker,
    agent_id: str,
    *,
    count: int = 15,
    quality: float = 8.0,
    is_success: bool = True,
) -> None:
    """Record multiple task metrics for an agent."""
    for i in range(count):
        record = TaskMetricRecord(
            agent_id=NotBlankStr(agent_id),
            task_id=NotBlankStr(f"task-{i:03d}"),
            task_type=TaskType.DEVELOPMENT,
            completed_at=datetime.now(UTC),
            is_success=is_success,
            duration_seconds=60.0,
            cost_usd=0.01,
            turns_used=5,
            tokens_used=1000,
            quality_score=quality,
            complexity=Complexity.MEDIUM,
        )
        await tracker.record_task_metric(record)


# ── Tests ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestEvaluatePromotion:
    """Tests for evaluate_promotion method."""

    async def test_eligible_agent(
        self,
        registry: AgentRegistryService,
        tracker: PerformanceTracker,
    ) -> None:
        """Agent with good metrics is evaluated as eligible."""
        identity = make_agent_identity(
            name="promotable-agent",
            level=SeniorityLevel.JUNIOR,
        )
        await registry.register(identity)
        agent_id = NotBlankStr(str(identity.id))
        await _seed_metrics(tracker, str(agent_id), quality=8.0)

        service = _make_service(registry=registry, tracker=tracker)
        evaluation = await service.evaluate_promotion(agent_id)

        assert evaluation.eligible is True
        assert evaluation.current_level == SeniorityLevel.JUNIOR
        assert evaluation.target_level == SeniorityLevel.MID

    async def test_raises_for_max_seniority(
        self,
        registry: AgentRegistryService,
        tracker: PerformanceTracker,
    ) -> None:
        """Raises PromotionError for agent at maximum seniority."""
        identity = make_agent_identity(
            name="max-level-agent",
            level=SeniorityLevel.C_SUITE,
        )
        await registry.register(identity)
        agent_id = NotBlankStr(str(identity.id))

        service = _make_service(registry=registry, tracker=tracker)
        with pytest.raises(PromotionError, match="maximum seniority"):
            await service.evaluate_promotion(agent_id)

    async def test_raises_for_unknown_agent(
        self,
        registry: AgentRegistryService,
        tracker: PerformanceTracker,
    ) -> None:
        """Raises PromotionError for an unknown agent ID."""
        service = _make_service(registry=registry, tracker=tracker)
        with pytest.raises(PromotionError, match="not found"):
            await service.evaluate_promotion(
                NotBlankStr("nonexistent-agent"),
            )


@pytest.mark.unit
class TestEvaluateDemotion:
    """Tests for evaluate_demotion method."""

    async def test_underperforming_agent(
        self,
        registry: AgentRegistryService,
        tracker: PerformanceTracker,
    ) -> None:
        """Agent with poor metrics is evaluated for demotion."""
        identity = make_agent_identity(
            name="underperformer",
            level=SeniorityLevel.MID,
        )
        await registry.register(identity)
        agent_id = NotBlankStr(str(identity.id))
        await _seed_metrics(
            tracker,
            str(agent_id),
            quality=2.0,
            is_success=False,
            count=5,
        )

        service = _make_service(registry=registry, tracker=tracker)
        evaluation = await service.evaluate_demotion(agent_id)

        assert evaluation.current_level == SeniorityLevel.MID
        assert evaluation.target_level == SeniorityLevel.JUNIOR


@pytest.mark.unit
class TestRequestPromotion:
    """Tests for request_promotion method."""

    async def test_auto_approved_junior_to_mid(
        self,
        registry: AgentRegistryService,
        tracker: PerformanceTracker,
    ) -> None:
        """Junior to Mid promotion request is auto-approved."""
        identity = make_agent_identity(
            name="auto-promote-agent",
            level=SeniorityLevel.JUNIOR,
        )
        await registry.register(identity)
        agent_id = NotBlankStr(str(identity.id))
        await _seed_metrics(tracker, str(agent_id), quality=8.0)

        service = _make_service(registry=registry, tracker=tracker)
        evaluation = await service.evaluate_promotion(agent_id)
        request = await service.request_promotion(agent_id, evaluation)

        assert request.status.value == "approved"
        assert request.agent_id == agent_id

    async def test_cooldown_raises_error(
        self,
        registry: AgentRegistryService,
        tracker: PerformanceTracker,
    ) -> None:
        """Second promotion request within cooldown raises error."""
        identity = make_agent_identity(
            name="cooldown-agent",
            level=SeniorityLevel.JUNIOR,
        )
        await registry.register(identity)
        agent_id = NotBlankStr(str(identity.id))
        await _seed_metrics(tracker, str(agent_id), quality=8.0)

        service = _make_service(registry=registry, tracker=tracker)
        evaluation = await service.evaluate_promotion(agent_id)
        request = await service.request_promotion(agent_id, evaluation)

        # Apply the first promotion to trigger cooldown
        await service.apply_promotion(request)

        # Second request should raise cooldown error
        with pytest.raises(PromotionCooldownError, match="cooldown"):
            await service.request_promotion(agent_id, evaluation)


@pytest.mark.unit
class TestRequestPromotionWithApprovalStore:
    """Tests for request_promotion with human approval + approval store."""

    async def test_human_approval_with_store_returns_pending(
        self,
        registry: AgentRegistryService,
        tracker: PerformanceTracker,
    ) -> None:
        """Request requiring human approval with store returns PENDING."""
        mock_store = AsyncMock(spec=ApprovalStore)

        # Mid -> Senior requires human approval by default
        identity = make_agent_identity(
            name="needs-approval-agent",
            level=SeniorityLevel.MID,
        )
        await registry.register(identity)
        agent_id = NotBlankStr(str(identity.id))
        await _seed_metrics(tracker, str(agent_id), quality=8.0)

        service = _make_service(
            registry=registry,
            tracker=tracker,
            approval_store=mock_store,
        )
        evaluation = await service.evaluate_promotion(agent_id)
        request = await service.request_promotion(agent_id, evaluation)

        assert request.status == ApprovalStatus.PENDING
        assert request.approval_id is not None
        mock_store.add.assert_awaited_once()

    async def test_ineligible_evaluation_raises(
        self,
        registry: AgentRegistryService,
        tracker: PerformanceTracker,
    ) -> None:
        """request_promotion with eligible=False raises PromotionError."""
        identity = make_agent_identity(
            name="ineligible-agent",
            level=SeniorityLevel.JUNIOR,
        )
        await registry.register(identity)
        agent_id = NotBlankStr(str(identity.id))
        # Seed poor metrics so evaluation yields eligible=False
        await _seed_metrics(
            tracker,
            str(agent_id),
            quality=2.0,
            is_success=False,
            count=3,
        )

        service = _make_service(registry=registry, tracker=tracker)
        evaluation = await service.evaluate_promotion(agent_id)

        assert evaluation.eligible is False

        with pytest.raises(PromotionError, match="not eligible"):
            await service.request_promotion(agent_id, evaluation)


@pytest.mark.unit
class TestApplyPromotion:
    """Tests for apply_promotion method."""

    async def test_updates_registry(
        self,
        registry: AgentRegistryService,
        tracker: PerformanceTracker,
    ) -> None:
        """apply_promotion updates the agent's level in the registry."""
        identity = make_agent_identity(
            name="apply-agent",
            level=SeniorityLevel.JUNIOR,
        )
        await registry.register(identity)
        agent_id = NotBlankStr(str(identity.id))
        await _seed_metrics(tracker, str(agent_id), quality=8.0)

        service = _make_service(registry=registry, tracker=tracker)
        evaluation = await service.evaluate_promotion(agent_id)
        request = await service.request_promotion(agent_id, evaluation)
        record = await service.apply_promotion(request)

        assert record.old_level == SeniorityLevel.JUNIOR
        assert record.new_level == SeniorityLevel.MID

        # Verify registry was updated
        updated_identity = await registry.get(agent_id)
        assert updated_identity is not None
        assert updated_identity.level == SeniorityLevel.MID

    async def test_raises_for_non_approved(
        self,
        registry: AgentRegistryService,
        tracker: PerformanceTracker,
    ) -> None:
        """apply_promotion raises for non-approved request."""
        mock_store = AsyncMock(spec=ApprovalStore)
        identity = make_agent_identity(
            name="pending-agent",
            level=SeniorityLevel.MID,
        )
        await registry.register(identity)
        agent_id = NotBlankStr(str(identity.id))
        await _seed_metrics(tracker, str(agent_id), quality=8.0)

        # Use config where Mid->Senior requires human approval
        service = _make_service(
            registry=registry,
            tracker=tracker,
            approval_store=mock_store,
        )
        evaluation = await service.evaluate_promotion(agent_id)
        request = await service.request_promotion(agent_id, evaluation)

        # Request should be PENDING (not auto-approved for SENIOR)
        assert request.status.value == "pending"

        with pytest.raises(
            PromotionApprovalRequiredError,
            match="request status is pending",
        ):
            await service.apply_promotion(request)

    async def test_model_changed_flag(
        self,
        registry: AgentRegistryService,
        tracker: PerformanceTracker,
    ) -> None:
        """apply_promotion tracks model changes in the record."""
        identity = make_agent_identity(
            name="model-change-agent",
            level=SeniorityLevel.JUNIOR,
            model_id="small",
        )
        await registry.register(identity)
        agent_id = NotBlankStr(str(identity.id))
        await _seed_metrics(tracker, str(agent_id), quality=8.0)

        service = _make_service(registry=registry, tracker=tracker)
        evaluation = await service.evaluate_promotion(agent_id)
        request = await service.request_promotion(agent_id, evaluation)
        record = await service.apply_promotion(request)

        # Junior -> Mid: "small" -> "medium" via role catalog
        assert record.model_changed is True
        assert str(record.old_model_id) == "small"
        assert str(record.new_model_id) == "medium"


@pytest.mark.unit
class TestGetPromotionHistory:
    """Tests for get_promotion_history method."""

    async def test_returns_history(
        self,
        registry: AgentRegistryService,
        tracker: PerformanceTracker,
    ) -> None:
        """get_promotion_history returns applied promotions."""
        config = PromotionConfig(cooldown_hours=0)
        identity = make_agent_identity(
            name="history-agent",
            level=SeniorityLevel.JUNIOR,
        )
        await registry.register(identity)
        agent_id = NotBlankStr(str(identity.id))
        await _seed_metrics(tracker, str(agent_id), quality=8.0)

        service = _make_service(
            registry=registry,
            tracker=tracker,
            config=config,
        )

        evaluation = await service.evaluate_promotion(agent_id)
        request = await service.request_promotion(agent_id, evaluation)
        await service.apply_promotion(request)

        history = service.get_promotion_history(agent_id)
        assert len(history) == 1
        assert history[0].old_level == SeniorityLevel.JUNIOR
        assert history[0].new_level == SeniorityLevel.MID

    async def test_empty_history_for_new_agent(
        self,
        registry: AgentRegistryService,
        tracker: PerformanceTracker,
    ) -> None:
        """get_promotion_history returns empty for agents with no promotions."""
        service = _make_service(registry=registry, tracker=tracker)
        history = service.get_promotion_history(NotBlankStr("unknown-agent"))
        assert history == ()


# ── _next_level / _prev_level helpers ────────────────────────────


@pytest.mark.unit
class TestLevelHelpers:
    """Tests for _next_level and _prev_level edge cases."""

    def test_next_level_at_c_suite_returns_none(self) -> None:
        """C_SUITE is the maximum level — _next_level returns None."""
        from synthorg.hr.promotion.service import _next_level

        assert _next_level(SeniorityLevel.C_SUITE) is None

    def test_next_level_junior_returns_mid(self) -> None:
        from synthorg.hr.promotion.service import _next_level

        assert _next_level(SeniorityLevel.JUNIOR) == SeniorityLevel.MID

    def test_prev_level_at_junior_returns_none(self) -> None:
        """JUNIOR is the minimum level — _prev_level returns None."""
        from synthorg.hr.promotion.service import _prev_level

        assert _prev_level(SeniorityLevel.JUNIOR) is None

    def test_prev_level_mid_returns_junior(self) -> None:
        from synthorg.hr.promotion.service import _prev_level

        assert _prev_level(SeniorityLevel.MID) == SeniorityLevel.JUNIOR


# ── evaluate_demotion edge cases ──────────────────────────────────


@pytest.mark.unit
class TestEvaluateDemotionEdgeCases:
    """Additional demotion edge cases."""

    async def test_raises_for_minimum_seniority(
        self,
        registry: AgentRegistryService,
        tracker: PerformanceTracker,
    ) -> None:
        """Raises PromotionError when agent is at minimum seniority."""
        identity = make_agent_identity(
            name="junior-agent",
            level=SeniorityLevel.JUNIOR,
        )
        await registry.register(identity)
        agent_id = NotBlankStr(str(identity.id))

        service = _make_service(registry=registry, tracker=tracker)
        with pytest.raises(PromotionError, match="minimum seniority"):
            await service.evaluate_demotion(agent_id)

    async def test_raises_for_unknown_agent(
        self,
        registry: AgentRegistryService,
        tracker: PerformanceTracker,
    ) -> None:
        """Raises PromotionError for unknown agent ID."""
        service = _make_service(registry=registry, tracker=tracker)
        with pytest.raises(PromotionError, match="not found"):
            await service.evaluate_demotion(
                NotBlankStr("nonexistent-agent"),
            )
