"""Plan-review service layer.

Thin wrapper over :class:`PlanRepository` so callers do not reach into
``app_state.persistence.plans`` directly. Owns the plan's lifecycle
transitions (edit -> new revision, request-changes -> back to draft, and the
approval-decision sync -> approved/rejected) with uniform ``API_PLAN_*`` audit
logging, mirroring :class:`ProjectService`. A terminal plan cannot be reworked,
and every write is version-guarded so a concurrent edit cannot silently clobber
another.
"""

from pydantic import ValidationError as PydanticValidationError

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import (
    ConflictError,
    ValidationError,
    VersionConflictError,
)
from synthorg.core.pagination import DEFAULT_PAGE_SIZE
from synthorg.core.persistence_errors import PersistenceVersionConflictError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import REWORKABLE_STATUSES, PlanStatus
from synthorg.core.task_enums import CoordinationTopology, TaskStructure
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_PLAN_CHANGES_REQUEST_FAILED,
    API_PLAN_CHANGES_REQUESTED,
    API_PLAN_FETCH_FAILED,
    API_PLAN_LIST_FAILED,
    API_PLAN_LISTED,
    API_PLAN_STATUS_TRANSITIONED,
    API_PLAN_TRANSITION_REJECTED,
    API_PLAN_UPDATE_FAILED,
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
        offset: int = 0,
    ) -> tuple[Plan, ...]:
        """List plans with optional ``status`` / ``project`` / ``objective`` filters.

        Returns:
            Matching plans in repository order, capped at *limit* rows and
            skipping the first *offset* rows.

        Raises:
            QueryError: Repository read failure (logged before propagating).
        """
        try:
            plans = await self._repo.query(
                PlanFilterSpec(
                    status=status, project=project, objective_id=objective_id
                ),
                limit=limit,
                offset=offset,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                API_PLAN_LIST_FAILED,
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
        DAG), bumps the version, and returns the plan to pending review. The
        caller (controller) maps request DTOs onto the domain ``items``; the
        service stays free of any ``api.dto_*`` dependency.

        Args:
            existing: The plan being reworked (already fetched by the caller).
            items: The revised domain items.
            task_structure: Optional override of the classified structure.
            coordination_topology: Optional override of the topology.

        Returns:
            The persisted, reworked plan.

        Raises:
            ConflictError: The plan is terminal (already decided) and cannot
                be reworked.
            ValidationError: The revised items violate a plan invariant.
            VersionConflictError: A concurrent write bumped the version first.
            RecordNotFoundError: The plan disappeared between fetch and write.
            QueryError: Repository write failure (logged before propagating).
        """
        self._require_reworkable(existing)
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
                API_PLAN_UPDATE_FAILED,
                plan_id=str(existing.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            detail = "; ".join(
                f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}"
                for e in exc.errors()
            )
            msg = f"Revised plan is invalid: {detail}"
            raise ValidationError(msg) from exc
        await self._persist_update(
            revised,
            expected_version=existing.version,
            failure_event=API_PLAN_UPDATE_FAILED,
        )
        logger.info(
            API_PLAN_UPDATED,
            plan_id=str(revised.id),
            version=revised.version,
            item_count=len(revised.items),
        )
        self._log_transition(existing.status, revised)
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
            ConflictError: The plan is terminal (already decided).
            VersionConflictError: A concurrent write bumped the version first.
            RecordNotFoundError: The plan disappeared between fetch and write.
            QueryError: Repository write failure (logged before propagating).
        """
        self._require_reworkable(existing)
        drafted = existing.model_copy(
            update={
                "status": PlanStatus.DRAFT,
                "version": existing.version + 1,
                "updated_at": self._clock.now(),
            }
        )
        await self._persist_update(
            drafted,
            expected_version=existing.version,
            failure_event=API_PLAN_CHANGES_REQUEST_FAILED,
        )
        logger.info(API_PLAN_CHANGES_REQUESTED, plan_id=str(drafted.id))
        self._log_transition(existing.status, drafted)
        return drafted

    async def sync_status(self, existing: Plan, status: PlanStatus) -> Plan:
        """Reflect an approval decision onto the plan (status -> approved/rejected).

        The single audited write path for the decision transition, so the
        approve/reject sync gets the same ``API_PLAN_*`` audit coverage as an
        operator edit rather than mutating the repository directly.

        Args:
            existing: The plan being decided (already fetched by the caller).
            status: The decision status to record.

        Returns:
            The persisted, decided plan.

        Raises:
            VersionConflictError: A concurrent write bumped the version first.
            RecordNotFoundError: The plan disappeared between fetch and write.
            QueryError: Repository write failure (logged before propagating).
        """
        decided = existing.model_copy(
            update={
                "status": status,
                "version": existing.version + 1,
                "updated_at": self._clock.now(),
            }
        )
        await self._persist_update(
            decided,
            expected_version=existing.version,
            failure_event=API_PLAN_UPDATE_FAILED,
        )
        self._log_transition(existing.status, decided)
        return decided

    def _require_reworkable(self, plan: Plan) -> None:
        """Reject an operator rework of a terminal (already-decided) plan.

        Raises:
            ConflictError: ``plan.status`` is terminal (APPROVED / REJECTED /
                SUPERSEDED), so it can no longer be reworked.
        """
        if plan.status in REWORKABLE_STATUSES:
            return
        logger.warning(
            API_PLAN_TRANSITION_REJECTED,
            plan_id=str(plan.id),
            status=plan.status.value,
            reason="terminal_plan_not_reworkable",
        )
        msg = (
            f"Plan {plan.id} is {plan.status.value} and can no longer be "
            "reworked (a decision has already been recorded)"
        )
        raise ConflictError(msg)

    def _log_transition(self, from_status: PlanStatus, plan: Plan) -> None:
        """Log a plan status transition after the persistence write succeeds."""
        if from_status == plan.status:
            return
        logger.info(
            API_PLAN_STATUS_TRANSITIONED,
            plan_id=str(plan.id),
            from_status=from_status.value,
            to_status=plan.status.value,
            version=plan.version,
        )

    async def _persist_update(
        self,
        plan: Plan,
        *,
        expected_version: int,
        failure_event: str,
    ) -> None:
        """Persist an updated plan under optimistic concurrency control.

        Args:
            plan: The revised plan to write (its ``version`` is the new value).
            expected_version: The version the caller read; the write only
                lands if the stored row still carries it.
            failure_event: Event constant to log a repository failure under.

        Raises:
            VersionConflictError: The stored version moved (concurrent write).
            RecordNotFoundError: No plan with this id exists.
            QueryError: Repository write failure.
        """
        try:
            await self._repo.update(plan, expected_version=expected_version)
        except PersistenceVersionConflictError as exc:
            logger.warning(
                failure_event,
                plan_id=str(plan.id),
                error_type=type(exc).__name__,
                reason="version_conflict",
            )
            msg = f"Plan {plan.id} was modified concurrently; re-read and retry"
            raise VersionConflictError(msg) from exc
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                failure_event,
                plan_id=str(plan.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
