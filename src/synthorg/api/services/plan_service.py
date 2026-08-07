# module-kind: service
"""Plan-review service layer.

Thin wrapper over :class:`PlanRepository` so callers do not reach into
``app_state.persistence.plans`` directly. Owns the plan's lifecycle
transitions (edit -> new revision, request-changes -> back to draft, and the
approval-decision sync -> approved/rejected) with uniform ``API_PLAN_*`` audit
logging, mirroring :class:`ProjectService`. A terminal plan cannot be reworked,
and every write is version-guarded so a concurrent edit cannot silently clobber
another.
"""

from typing import Final

from pydantic import ValidationError as PydanticValidationError

from synthorg.api.services._plan_revision import (
    build_successor,
    describe_validation_error,
    extended_history,
    require_replannable,
    require_reworkable,
)
from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import (
    ConflictError,
    PlanNotDeletableError,
    ServiceUnavailableError,
    ValidationError,
    VersionConflictError,
)
from synthorg.core.pagination import DEFAULT_PAGE_SIZE
from synthorg.core.persistence_errors import PersistenceVersionConflictError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import (
    DELETABLE_STATUSES,
    REPLANNABLE_STATUSES,
    TAIL_STATUSES,
    PlanStatus,
)
from synthorg.core.plan_transitions import validate_transition
from synthorg.core.task_enums import CoordinationTopology, TaskStructure
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_PLAN_CHANGES_REQUEST_FAILED,
    API_PLAN_CHANGES_REQUESTED,
    API_PLAN_DELETE_REFUSED,
    API_PLAN_DELETED,
    API_PLAN_FETCH_FAILED,
    API_PLAN_LIST_FAILED,
    API_PLAN_LISTED,
    API_PLAN_STATUS_TRANSITIONED,
    API_PLAN_SUCCESSOR_OPENED,
    API_PLAN_TRANSITION_REJECTED,
    API_PLAN_UPDATE_FAILED,
    API_PLAN_UPDATED,
)
from synthorg.persistence.evaluation_report_protocol import (
    EvaluationReportFilterSpec,
    EvaluationReportRecord,
    EvaluationReportRepository,
)
from synthorg.persistence.plan_protocol import PlanFilterSpec, PlanRepository

logger = get_logger(__name__)


#: Judgement history a single read returns. The evaluate stage caps its own
#: attempts well below this, so the page holds every verdict a plan can have
#: while still refusing to stream an unbounded history to a caller.
MAX_EVALUATION_ATTEMPTS: Final[int] = 20


class PlanService:
    """Wraps :class:`PlanRepository` with uniform audit logging.

    Args:
        repo: Plan repository implementation.
        clock: Time seam; edits/transitions stamp ``updated_at`` from it.
        evaluation_reports: Judgement store backing
            :meth:`evaluation_history`. Optional because most callers build
            this service purely as the audited plan-status writer and never
            read a verdict.
    """

    __slots__ = ("_clock", "_evaluation_reports", "_repo")

    _repo: PlanRepository
    _clock: Clock
    _evaluation_reports: EvaluationReportRepository | None

    def __init__(
        self,
        *,
        repo: PlanRepository,
        clock: Clock,
        evaluation_reports: EvaluationReportRepository | None = None,
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._evaluation_reports = evaluation_reports

    async def evaluation_history(
        self, plan_id: NotBlankStr
    ) -> tuple[EvaluationReportRecord, ...]:
        """Return the evaluate stage's judgements for *plan_id*, newest first.

        Returns:
            The recorded judgements, bounded by
            :data:`MAX_EVALUATION_ATTEMPTS`.

        Raises:
            ServiceUnavailableError: When this service was built without a
                judgement store, so an empty history cannot be told apart
                from a plan that has never been judged.
        """
        if self._evaluation_reports is None:
            msg = "Plan evaluation history is unavailable: no judgement store wired"
            raise ServiceUnavailableError(msg)
        return await self._evaluation_reports.query(
            EvaluationReportFilterSpec(plan_id=plan_id),
            limit=MAX_EVALUATION_ATTEMPTS,
        )

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
        require_reworkable(existing)
        # Snapshot the pre-edit version so a reviewer can diff the rework against
        # what the panel saw, capped so the JSON column cannot grow unbounded.
        history = extended_history(existing)
        try:
            revised = Plan(
                id=existing.id,
                project=existing.project,
                objective_id=existing.objective_id,
                objective_title=existing.objective_title,
                parent_task_id=existing.parent_task_id,
                items=items,
                task_structure=task_structure or existing.task_structure,
                coordination_topology=(
                    coordination_topology or existing.coordination_topology
                ),
                status=PlanStatus.PENDING_REVIEW,
                forecast_id=existing.forecast_id,
                # A revision invalidates the prior panel's findings (they
                # reference the pre-edit items); the plan re-enters review with
                # no stale verdict shown against the new version.
                review=None,
                open_questions=existing.open_questions,
                assumptions=existing.assumptions,
                objective_criteria=existing.objective_criteria,
                version_history=history,
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
            msg = f"Revised plan is invalid: {describe_validation_error(exc)}"
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

    async def request_changes(self, existing: Plan, *, note: str | None = None) -> Plan:
        """Send a plan back for revision (status -> draft).

        The operator's *note* is recorded on the durable audit event so the
        rationale outlives the transient WebSocket notification; turning it
        into a concrete replan is the wiring layer's concern.

        Args:
            existing: The plan being sent back (already fetched by the caller).
            note: The operator's rationale for the change request, recorded on
                the ``API_PLAN_CHANGES_REQUESTED`` audit event.

        Returns:
            The persisted, drafted plan.

        Raises:
            ConflictError: The plan is terminal (already decided).
            VersionConflictError: A concurrent write bumped the version first.
            RecordNotFoundError: The plan disappeared between fetch and write.
            QueryError: Repository write failure (logged before propagating).
        """
        require_reworkable(existing)
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
        logger.info(API_PLAN_CHANGES_REQUESTED, plan_id=str(drafted.id), note=note)
        self._log_transition(existing.status, drafted)
        return drafted

    @staticmethod
    def _require_deletable(plan: Plan) -> None:
        """Refuse a delete that would destroy a record rather than a request.

        Raises:
            PlanNotDeletableError: When the plan is dispatched (its items are
                already building, so it is what those tasks were approved
                against) or terminal (it is the record of what was decided,
                and its delivery verdicts cascade off the row).
        """
        if plan.status in DELETABLE_STATUSES:
            return
        logger.info(
            API_PLAN_DELETE_REFUSED,
            plan_id=str(plan.id),
            status=plan.status.value,
        )
        building = plan.status in REPLANNABLE_STATUSES | TAIL_STATUSES
        detail = (
            "its items are building; replan it instead of deleting it"
            if building
            else "already decided; its record and its verdicts outlive it"
        )
        msg = f"plan {plan.id} is {plan.status.value} and is {detail}"
        raise PlanNotDeletableError(msg)

    async def delete(self, existing: Plan, *, requested_by: str) -> None:
        """Remove a request that never became work.

        The route exists to clear a plan an operator has decided not to
        pursue: a shell whose decomposition stranded, a draft, one waiting
        on review, or one that failed. Every other status is refused, and
        the refusal routes through here rather than the controller so the
        one irreversible plan operation is audited on the same path as
        every reversible one.

        Args:
            existing: The plan being removed (already fetched by the caller).
            requested_by: Who asked, recorded on the audit event.

        Raises:
            PlanNotDeletableError: The plan is dispatched or terminal.
            QueryError: Repository write failure.
        """
        self._require_deletable(existing)
        await self._repo.delete(NotBlankStr(str(existing.id)))
        logger.info(
            API_PLAN_DELETED,
            plan_id=str(existing.id),
            status=existing.status.value,
            requested_by=requested_by,
        )

    async def sync_status(
        self,
        existing: Plan,
        status: PlanStatus,
        *,
        requested_by: str | None = None,
        reason: str | None = None,
        failure_reason: NotBlankStr | None = None,
    ) -> Plan:
        """Reflect an approval decision onto the plan (status -> approved/rejected).

        The single audited write path for the decision transition, so the
        approve/reject sync gets the same ``API_PLAN_*`` audit coverage as an
        operator edit rather than mutating the repository directly.

        Args:
            existing: The plan being decided (already fetched by the caller).
            status: The decision status to record.
            requested_by: Identity driving the transition, recorded on the
                audit log so a cascade supersede keeps the same actor context
                a task transition retains.
            reason: Why the transition happened (e.g. a project teardown),
                recorded on the audit log alongside ``requested_by``.
            failure_reason: Why the plan failed, persisted ON the plan so Plan
                Review shows it. Required by the model (and the column check)
                exactly when *status* is FAILED, and rejected otherwise, so it
                is passed only alongside that status.

        Returns:
            The persisted, decided plan.

        Raises:
            ConflictError: The transition is not legal for the plan lifecycle.
            ValidationError: FAILED was requested with no ``failure_reason``.
            VersionConflictError: A concurrent write bumped the version first.
            RecordNotFoundError: The plan disappeared between fetch and write.
            QueryError: Repository write failure (logged before propagating).
        """
        self._require_legal_transition(existing, status)
        failing = status is PlanStatus.FAILED
        if failing and failure_reason is None:
            msg = "a plan may only be failed with a reason Plan Review can show"
            raise ValidationError(msg)
        now = self._clock.now()
        # ``Plan.fail`` owns the status/reason pairing, which ``model_copy``
        # cannot police on its own: it does not re-run validators.
        decided = (
            existing.fail(failure_reason, now=now)
            if failing and failure_reason is not None
            else existing.model_copy(
                update={
                    "status": status,
                    "version": existing.version + 1,
                    "updated_at": now,
                }
            )
        )
        await self._persist_update(
            decided,
            expected_version=existing.version,
            failure_event=API_PLAN_UPDATE_FAILED,
        )
        self._log_transition(
            existing.status, decided, requested_by=requested_by, reason=reason
        )
        return decided

    def _require_legal_transition(self, plan: Plan, target: PlanStatus) -> None:
        """Reject a status write the plan lifecycle does not allow.

        Every plan status write funnels through :meth:`sync_status`, so
        validating here means an illegal hop (completing a plan that never
        executed, reopening a terminal one) cannot be persisted from any
        caller.

        Raises:
            ConflictError: The transition is not in the plan state machine.
        """
        if plan.status == target:
            return
        try:
            validate_transition(plan.status, target)
        except ValueError as exc:
            logger.warning(
                API_PLAN_TRANSITION_REJECTED,
                plan_id=str(plan.id),
                status=plan.status.value,
                target=target.value,
                reason="illegal_transition",
            )
            msg = (
                f"Plan {plan.id} cannot move from {plan.status.value} to {target.value}"
            )
            raise ConflictError(msg) from exc

    async def open_successor(
        self,
        existing: Plan,
        *,
        items: tuple[PlanItem, ...],
        task_structure: TaskStructure | None = None,
        coordination_topology: CoordinationTopology | None = None,
        replan_generation: int = 0,
    ) -> Plan:
        """Create the revision that replaces a dispatched plan.

        A dispatched plan cannot be edited in place: its items are already
        building, so a revision is a new plan entity that supersedes it rather
        than a version bump. The successor carries the retired plan's objective
        and framing forward and re-enters review, because its items have not
        been approved.

        The caller retires *existing* and repoints the project; this method
        only builds and persists the successor.

        Args:
            existing: The dispatched plan being replaced.
            items: The revised domain items.
            task_structure: Optional override of the classified structure.
            coordination_topology: Optional override of the topology.
            replan_generation: Generation to stamp on the successor. Left at
                zero for a human replan; the automatic trigger passes the
                predecessor's generation plus one so its chain stays capped.

        Returns:
            The persisted successor, awaiting review.

        Raises:
            ConflictError: *existing* is not a dispatched plan, so it should be
                edited in place rather than replanned.
            ValidationError: The revised items violate a plan invariant.
            QueryError: Repository write failure (logged before propagating).
        """
        require_replannable(existing)
        try:
            successor = build_successor(
                existing,
                items=items,
                task_structure=task_structure,
                coordination_topology=coordination_topology,
                now=self._clock.now(),
                replan_generation=replan_generation,
            )
        except PydanticValidationError as exc:
            logger.warning(
                API_PLAN_UPDATE_FAILED,
                plan_id=str(existing.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Successor plan is invalid: {describe_validation_error(exc)}"
            raise ValidationError(msg) from exc
        try:
            await self._repo.save(successor)
        except Exception as exc:
            reraise_critical(exc)
            # The caller has already retired the current revision, so a lost
            # successor leaves the initiative with no live plan. Log before
            # propagating or that state is undiagnosable.
            logger.warning(
                API_PLAN_UPDATE_FAILED,
                plan_id=str(successor.id),
                supersedes=str(existing.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.info(
            API_PLAN_SUCCESSOR_OPENED,
            plan_id=str(successor.id),
            supersedes=str(existing.id),
            item_count=len(successor.items),
        )
        return successor

    def _log_transition(
        self,
        from_status: PlanStatus,
        plan: Plan,
        *,
        requested_by: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Log a plan status transition after the persistence write succeeds."""
        if from_status == plan.status:
            return
        context: dict[str, str] = {}
        if requested_by is not None:
            context["requested_by"] = requested_by
        if reason is not None:
            context["reason"] = reason
        logger.info(
            API_PLAN_STATUS_TRANSITIONED,
            plan_id=str(plan.id),
            from_status=from_status.value,
            to_status=plan.status.value,
            version=plan.version,
            **context,
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
