"""Unit tests for :class:`TrainingPlanService`.

The service is the audit-aware facade ``TrainingController`` routes
its plan + result writes through.  These tests pin:

- Each method delegates to the right repo with the right object.
- ``update_overrides`` applies the diff before saving and returns
  the new plan instance.
- ``record_failure`` flips the status to FAILED + sets executed_at,
  and swallows persistence errors so the original execute exception
  bubbles up unchanged in the controller.
- ``record_executed`` flips to EXECUTED and persists both plan and
  result (in that order).

Audit-event emission itself is exercised by the integration tests
under ``tests/integration/api/`` (controller-level), where the full
logging pipeline is wired; structlog's interaction with pytest's
``caplog`` is too brittle to assert on directly here without making
the test report carry a structlog-specific failure mode.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import SeniorityLevel
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.hr.training.models import (
    ContentType,
    TrainingPlan,
    TrainingPlanStatus,
    TrainingResult,
)
from synthorg.hr.training.plan_service import TrainingPlanService
from synthorg.persistence.training_repos import (
    TrainingPlanRepository,
    TrainingResultRepository,
)

pytestmark = pytest.mark.unit


def _plan(plan_id: str = "plan-1", agent_id: str = "agent-1") -> TrainingPlan:
    """Build a minimal valid TrainingPlan for the tests."""
    return TrainingPlan(
        id=NotBlankStr(plan_id),
        new_agent_id=NotBlankStr(agent_id),
        new_agent_role=NotBlankStr("developer"),
        new_agent_level=SeniorityLevel.SENIOR,
        new_agent_department=NotBlankStr("eng"),
        override_sources=(),
        enabled_content_types=frozenset({ContentType.PROCEDURAL}),
        skip_training=False,
        require_review=True,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _result(plan_id: str = "plan-1", agent_id: str = "agent-1") -> TrainingResult:
    """Build a minimal TrainingResult mapped to ``plan_id``."""
    return TrainingResult(
        id=NotBlankStr(f"res-{plan_id}"),
        plan_id=NotBlankStr(plan_id),
        new_agent_id=NotBlankStr(agent_id),
        started_at=datetime(2026, 1, 2, tzinfo=UTC),
        completed_at=datetime(2026, 1, 2, tzinfo=UTC),
    )


def _build_service() -> tuple[TrainingPlanService, AsyncMock, AsyncMock]:
    """Build a service backed by spec'd async-mocked repositories."""
    plan_repo = AsyncMock(spec=TrainingPlanRepository)
    result_repo = AsyncMock(spec=TrainingResultRepository)
    service = TrainingPlanService(plan_repo=plan_repo, result_repo=result_repo)
    return service, plan_repo, result_repo


class TestCreatePlan:
    async def test_persists_plan_and_returns_it_unchanged(self) -> None:
        service, plan_repo, _ = _build_service()
        plan = _plan()
        returned = await service.create_plan(plan)
        plan_repo.save.assert_awaited_once_with(plan)
        assert returned is plan


class TestUpdateOverrides:
    async def test_applies_updates_and_persists_new_plan(self) -> None:
        service, plan_repo, _ = _build_service()
        plan = _plan()
        updates: dict[str, object] = {"skip_training": True}
        updated = await service.update_overrides(plan, updates=updates)
        # The returned plan reflects the diff but the original stays intact
        # (frozen Pydantic immutability).
        assert updated.skip_training is True
        assert plan.skip_training is False
        plan_repo.save.assert_awaited_once()
        saved = plan_repo.save.await_args.args[0]
        assert saved.skip_training is True
        assert saved.id == plan.id

    async def test_no_updates_still_persists_a_copy(self) -> None:
        """Empty updates is a legal no-op save -- mirrors model_copy semantics."""
        service, plan_repo, _ = _build_service()
        plan = _plan()
        updated = await service.update_overrides(plan, updates={})
        # ``model_copy(update={})`` returns a fresh instance, so the
        # returned object is not the input but is field-equal to it.
        assert updated == plan
        plan_repo.save.assert_awaited_once()


class TestRecordFailure:
    async def test_persists_failed_plan_with_executed_at(self) -> None:
        service, plan_repo, _ = _build_service()
        plan = _plan()
        await service.record_failure(plan)
        plan_repo.save.assert_awaited_once()
        saved = plan_repo.save.await_args.args[0]
        assert saved.status == TrainingPlanStatus.FAILED
        assert saved.executed_at is not None
        # The original plan must not mutate -- frozen-by-default.
        assert plan.status != TrainingPlanStatus.FAILED

    async def test_persistence_error_is_swallowed(self) -> None:
        """Persistence errors swallowed; original execute exception bubbles.

        ``record_failure`` is called from the controller's
        ``execute_plan`` exception path AFTER a pipeline exception
        already raised.  Swallowing a persistence-side save failure
        here keeps the original failure as the one the caller sees.
        Non-persistence exceptions still propagate.
        """
        service, plan_repo, _ = _build_service()
        plan_repo.save.side_effect = QueryError("backend down")
        # No exception bubbles for persistence errors.
        await service.record_failure(_plan())
        plan_repo.save.assert_awaited_once()

    async def test_non_persistence_error_propagates(self) -> None:
        """Programming errors (TypeError etc.) escape the swallow scope."""
        service, plan_repo, _ = _build_service()
        plan_repo.save.side_effect = TypeError("programming bug")
        with pytest.raises(TypeError):
            await service.record_failure(_plan())


class TestRecordExecuted:
    async def test_persists_plan_then_result(self) -> None:
        """Plan save MUST precede result save (FK-style ordering invariant).

        Records the await sequence on a shared list so the assertion
        catches a future refactor that flips the order -- the prior
        ``assert_awaited_once`` form on each mock independently would
        accept ``result_repo.save`` first silently.
        """
        service, plan_repo, result_repo = _build_service()
        call_order: list[str] = []

        async def _record_plan_save(_: object) -> None:
            call_order.append("plan_saved")

        async def _record_result_save(_: object) -> None:
            call_order.append("result_saved")

        plan_repo.save.side_effect = _record_plan_save
        result_repo.save.side_effect = _record_result_save
        plan = _plan()
        result = _result()
        await service.record_executed(plan, result)
        plan_repo.save.assert_awaited_once()
        executed = plan_repo.save.await_args.args[0]
        assert executed.status == TrainingPlanStatus.EXECUTED
        assert executed.executed_at == result.completed_at
        result_repo.save.assert_awaited_once_with(result)
        assert call_order == ["plan_saved", "result_saved"]
