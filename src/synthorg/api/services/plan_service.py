"""Plan-review service layer.

Thin wrapper over :class:`PlanRepository` so the ``/plans`` controller does
not reach into ``app_state.persistence.plans`` directly. Owns the plan's
operator-facing lifecycle transitions (edit -> new revision, request-changes
-> back to draft) with uniform ``API_PLAN_*`` audit logging, mirroring
:class:`ProjectService`.
"""

from pydantic import ValidationError as PydanticValidationError

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ValidationError
from synthorg.core.pagination import DEFAULT_PAGE_SIZE
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task_enums import CoordinationTopology, TaskStructure
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_PLAN_CHANGES_REQUESTED,
    API_PLAN_FETCH_FAILED,
    API_PLAN_LISTED,
    API_PLAN_UPDATED,
)
from synthorg.persistence.plan_protocol import PlanFilterSpec, PlanRepository

logger = get_logger(__name__)


class PlanService:
    """Wraps :class:`PlanRepository` with uniform audit logging.

    Args:
        repo: Plan repository implementation.
        clock: Time seam; edits/transitions stamp ``updated_at`` from it.
    """

    __slots__ = ("_clock", "_repo")

    _repo: PlanRepository
    _clock: Clock

    def __init__(self, *, repo: PlanRepository, clock: Clock) -> None:
        self._repo = repo
        self._clock = clock

    async def get(self, plan_id: NotBlankStr) -> Plan | None:
        """Fetch a plan by id.

        Returns:
            The plan, or ``None`` when no row matches.

        Raises:
            QueryError: Repository read failure (logged before propagating).
        """
        try:
            return await self._repo.get(plan_id)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                API_PLAN_FETCH_FAILED,
                plan_id=plan_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    async def list_plans(
        self,
        *,
        status: PlanStatus | None = None,
        project: NotBlankStr | None = None,
        objective_id: NotBlankStr | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[Plan, ...]:
        """List plans with optional ``status`` / ``project`` / ``objective`` filters.

        Returns:
            Matching plans in repository order, capped at *limit* rows.

        Raises:
            QueryError: Repository read failure (logged before propagating).
        """
        try:
            plans = await self._repo.query(
                PlanFilterSpec(
                    status=status, project=project, objective_id=objective_id
                ),
                limit=limit,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                API_PLAN_LISTED,
                status=status.value if status is not None else None,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.debug(API_PLAN_LISTED, count=len(plans))
        return plans

    async def edit(
        self,
        existing: Plan,
        *,
        items: tuple[PlanItem, ...],
        task_structure: TaskStructure | None = None,
        coordination_topology: CoordinationTopology | None = None,
    ) -> Plan:
        """Apply an operator rework, producing a new revision under review.

        Replaces the plan's items wholesale (revalidating the dependency
        DAG), bumps the version, and returns the plan to pending review.
        The caller (controller) maps request DTOs onto the domain ``items``;
        the service stays free of any ``api.dto_*`` dependency.

        Args:
            existing: The plan being reworked (already fetched by the caller).
            items: The revised domain items.
            task_structure: Optional override of the classified structure.
            coordination_topology: Optional override of the topology.

        Returns:
            The persisted, reworked plan.

        Raises:
            ValidationError: The revised items violate a plan invariant
                (duplicate ids, unresolvable dependency, self-cycle).
            RecordNotFoundError: The plan disappeared between fetch and write.
            QueryError: Repository write failure (logged before propagating).
        """
        try:
            revised = Plan(
                id=existing.id,
                project=existing.project,
                objective_id=existing.objective_id,
                parent_task_id=existing.parent_task_id,
                items=items,
                task_structure=task_structure or existing.task_structure,
                coordination_topology=(
                    coordination_topology or existing.coordination_topology
                ),
                status=PlanStatus.PENDING_REVIEW,
                forecast_id=existing.forecast_id,
                version=existing.version + 1,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
            )
        except PydanticValidationError as exc:
            logger.warning(
                API_PLAN_UPDATED,
                plan_id=str(existing.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Revised plan is invalid (check item ids and dependencies)"
            raise ValidationError(msg) from exc
        await self._persist_update(revised, event=API_PLAN_UPDATED)
        logger.info(
            API_PLAN_UPDATED,
            plan_id=str(revised.id),
            version=revised.version,
            item_count=len(revised.items),
        )
        return revised

    async def request_changes(self, existing: Plan) -> Plan:
        """Send a plan back for revision (status -> draft).

        The operator's note is surfaced by the controller (WS event + audit);
        turning it into a concrete replan is the wiring layer's concern.

        Args:
            existing: The plan being sent back (already fetched by the caller).

        Returns:
            The persisted, drafted plan.

        Raises:
            RecordNotFoundError: The plan disappeared between fetch and write.
            QueryError: Repository write failure (logged before propagating).
        """
        drafted = existing.model_copy(
            update={
                "status": PlanStatus.DRAFT,
                "updated_at": self._clock.now(),
            }
        )
        await self._persist_update(drafted, event=API_PLAN_CHANGES_REQUESTED)
        logger.info(API_PLAN_CHANGES_REQUESTED, plan_id=str(drafted.id))
        return drafted

    async def _persist_update(self, plan: Plan, *, event: str) -> None:
        """Persist an updated plan, logging repository failures under *event*.

        Raises:
            RecordNotFoundError: No plan with this id exists.
            QueryError: Repository write failure.
        """
        try:
            await self._repo.update(plan)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                event,
                plan_id=str(plan.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
