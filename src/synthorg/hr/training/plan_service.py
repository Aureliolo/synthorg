"""TrainingPlanService -- audit-aware facade over the plan + result repos.

The :class:`TrainingController` previously called
``persistence.training_plans.save(...)`` and
``persistence.training_results.save(...)`` directly.  Routing every
write through this service centralises:

1. The four save sites (create, overrides update, failure, executed +
   result), each emitting a structured audit event so the durable
   write is always observable.
2. The model-copy + status transitions for the failure / executed
   paths so the controller stops constructing intermediate
   :class:`TrainingPlan` snapshots inline.

The service deliberately exposes a narrow surface (four async
methods) that map one-to-one onto the controller call sites; broader
plan-lifecycle orchestration (preview, execute, source selection,
guards) stays on :class:`TrainingService`, which this service
complements rather than replaces.

Every other persistence-layer mutation in the controller stack
already routes through a service for the same reason; centralising
this one closes the remaining gap.
"""

from collections.abc import Mapping  # noqa: TC003 -- runtime annotation
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.core.persistence_errors import PersistenceError
from synthorg.hr.training.models import (
    TrainingPlan,
    TrainingPlanStatus,
    TrainingResult,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.training import (
    HR_TRAINING_PLAN_CREATED,
    HR_TRAINING_PLAN_EXECUTED,
    HR_TRAINING_PLAN_FAILED,
    HR_TRAINING_PLAN_OVERRIDES_UPDATED,
)

if TYPE_CHECKING:
    from synthorg.persistence.training_repos import (
        TrainingPlanRepository,
        TrainingResultRepository,
    )

logger = get_logger(__name__)


class TrainingPlanService:
    """Audit-aware facade over the training plan + result repositories.

    Owns every write path the controller previously exercised on
    :attr:`PersistenceBackend.training_plans` and
    :attr:`PersistenceBackend.training_results` so audit logging
    cannot silently regress when a new write path is added.

    Args:
        plan_repo: Repository handling :class:`TrainingPlan` rows.
        result_repo: Repository handling :class:`TrainingResult` rows.
    """

    __slots__ = ("_plan_repo", "_result_repo")

    def __init__(
        self,
        *,
        plan_repo: TrainingPlanRepository,
        result_repo: TrainingResultRepository,
    ) -> None:
        self._plan_repo = plan_repo
        self._result_repo = result_repo

    async def create_plan(self, plan: TrainingPlan) -> TrainingPlan:
        """Persist a freshly created plan and emit the creation audit event.

        The caller is responsible for constructing the
        :class:`TrainingPlan` (the controller still owns DTO -> model
        translation); this method owns the durable write + audit log.
        """
        await self._plan_repo.save(plan)
        logger.info(
            HR_TRAINING_PLAN_CREATED,
            plan_id=str(plan.id),
            agent_id=str(plan.new_agent_id),
        )
        return plan

    async def update_overrides(
        self,
        plan: TrainingPlan,
        *,
        updates: Mapping[str, object],
    ) -> TrainingPlan:
        """Apply ``updates`` to ``plan``, persist, and emit the audit event.

        Returns the updated plan so the caller can serialise it onto
        the response without re-fetching.
        """
        updated = plan.model_copy(update=dict(updates))
        await self._plan_repo.save(updated)
        logger.info(
            HR_TRAINING_PLAN_OVERRIDES_UPDATED,
            plan_id=str(updated.id),
            agent_id=str(updated.new_agent_id),
            fields_changed=sorted(updates.keys()),
        )
        return updated

    async def record_failure(self, plan: TrainingPlan) -> None:
        """Mark ``plan`` FAILED and persist; swallow only persistence errors.

        Called from the controller's ``execute_plan`` exception path
        AFTER a pipeline failure has already been raised.  The
        durable status update is best-effort: if persistence rejects
        the save we WARN with the original ``plan_id`` plus the
        scrubbed error and continue, so the original execute-time
        exception still bubbles to the caller.

        The catch is narrowed to :class:`PersistenceError` so a
        non-persistence bug (typing, validation, programming error)
        in this code path surfaces as the unexpected error it is
        rather than getting swallowed under the best-effort label.
        """
        failed_plan = plan.model_copy(
            update={
                "status": TrainingPlanStatus.FAILED,
                "executed_at": datetime.now(UTC),
            }
        )
        try:
            await self._plan_repo.save(failed_plan)
        except PersistenceError as save_exc:
            logger.warning(
                HR_TRAINING_PLAN_FAILED,
                plan_id=str(plan.id),
                error="Failed to persist FAILED status",
                error_type=type(save_exc).__name__,
                persistence_error=safe_error_description(save_exc),
                exc_info=True,
            )
            return
        logger.info(
            HR_TRAINING_PLAN_FAILED,
            plan_id=str(failed_plan.id),
            agent_id=str(failed_plan.new_agent_id),
            from_status=plan.status.value,
            to_status=TrainingPlanStatus.FAILED.value,
        )

    async def record_executed(
        self,
        plan: TrainingPlan,
        result: TrainingResult,
    ) -> None:
        """Persist the EXECUTED plan + result pair and emit one audit event.

        The two saves do not share a transaction (each repo owns its
        own connection); a result write that fails after the plan
        save still leaves the plan in EXECUTED so the audit event
        reflects the persisted state.
        """
        executed_plan = plan.model_copy(
            update={
                "status": TrainingPlanStatus.EXECUTED,
                "executed_at": result.completed_at,
            }
        )
        await self._plan_repo.save(executed_plan)
        # Emit the durable status-transition event between the two
        # writes so an EXECUTED plan whose result row fails to land is
        # still recorded in the audit trail at the correct hop.
        logger.info(
            HR_TRAINING_PLAN_EXECUTED,
            plan_id=str(executed_plan.id),
            agent_id=str(executed_plan.new_agent_id),
            from_status=plan.status.value,
            to_status=TrainingPlanStatus.EXECUTED.value,
        )
        await self._result_repo.save(result)


__all__ = ["TrainingPlanService"]
