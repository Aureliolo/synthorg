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
from synthorg.api.services.plan_service_writes import PlanWriteRecorderMixin
from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import (
    ConflictError,
    PlanNotDeletableError,
    ValidationError,
)
from synthorg.core.pagination import DEFAULT_PAGE_SIZE
from synthorg.core.persistence_errors import RecordNotFoundError
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
from synthorg.engine.task_engine_apply_helpers import TRULY_TERMINAL_STATUSES
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_PLAN_CHANGES_REQUEST_FAILED,
    API_PLAN_CHANGES_REQUESTED,
    API_PLAN_DELETE_REFUSED,
    API_PLAN_DELETED,
    API_PLAN_FETCH_FAILED,
    API_PLAN_LIST_FAILED,
    API_PLAN_LISTED,
    API_PLAN_SUCCESSOR_OPENED,
    API_PLAN_TRANSITION_REJECTED,
    API_PLAN_UPDATE_FAILED,
    API_PLAN_UPDATED,
)
from synthorg.persistence.lifecycle_ledger import LifecycleLedger
from synthorg.persistence.lifecycle_transition_protocol import (
    LifecycleTransitionRepository,
)
from synthorg.persistence.plan_protocol import PlanFilterSpec, PlanRepository

logger = get_logger(__name__)

#: Task status values the guarded delete reads as finished, derived from the
#: engine's terminal set rather than restated, so a new terminal status cannot
#: be missed here. Rendered to the persisted wire values because the guard runs
#: as SQL against the status column.
TERMINAL_TASK_STATUS_VALUES: Final[frozenset[str]] = frozenset(
    status.value for status in TRULY_TERMINAL_STATUSES
)


class PlanService(PlanWriteRecorderMixin):
    """Wraps :class:`PlanRepository` with uniform audit logging.

    Every plan write funnels through here, so the audit line, the terminal
    guard and the version-conflict translation have one definition. Reading
    the evaluate stage's verdicts is a different store and a different
    responsibility, and lives in
    :class:`~synthorg.api.services.plan_evaluation_service.PlanEvaluationService`.

    Args:
        repo: Plan repository implementation.
        clock: Time seam; edits/transitions stamp ``updated_at`` from it.
        transitions: Append-only ledger every status write is recorded in.
            Required, not optional: a service built without it would persist
            a status and log the transition while the durable record of who
            moved the plan silently never happened, which is the exact gap
            the ledger exists to close. Build through
            :func:`~synthorg.api.services.plan_service_factory.build_plan_service`
            rather than passing it by hand.
    """

    __slots__ = ("_clock", "_ledger", "_repo")

    _repo: PlanRepository
    _clock: Clock
    _ledger: LifecycleLedger

    def __init__(
        self,
        *,
        repo: PlanRepository,
        clock: Clock,
        transitions: LifecycleTransitionRepository,
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._ledger = LifecycleLedger(transitions, clock=clock)

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
        await self._log_transition(existing.status, revised)
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
        await self._log_transition(existing.status, drafted)
        return drafted

    @staticmethod
    def _require_deletable_status(plan: Plan) -> None:
        """Refuse a delete on a status that is a record, not a request.

        A terminal plan is what was decided, and its delivery verdicts hang
        off the row, so it outlives the decision to stop pursuing it. Every
        other status may be deleted subject to the live-work guard below,
        which is the database's answer rather than this one.

        Raises:
            PlanNotDeletableError: The plan is terminal.
        """
        if plan.status in DELETABLE_STATUSES | REPLANNABLE_STATUSES | TAIL_STATUSES:
            return
        logger.info(
            API_PLAN_DELETE_REFUSED,
            plan_id=str(plan.id),
            status=plan.status.value,
            reason="already_decided",
        )
        msg = (
            f"plan {plan.id} is {plan.status.value} and already decided; "
            "its record and its verdicts outlive it"
        )
        raise PlanNotDeletableError(msg)

    async def delete(self, existing: Plan, *, requested_by: str) -> None:
        """Remove a request that never became work.

        The route exists to clear a plan an operator has decided not to
        pursue: a shell whose decomposition stranded, a draft, one waiting
        on review, one that failed, or a dispatched one whose tasks never
        made it onto the board. A terminal plan is refused, and so is any
        plan with work still building under it. The refusal routes through
        here rather than the controller so the one irreversible plan
        operation is audited on the same path as every reversible one.

        Live work is not counted here and then deleted afterwards: the count
        and the delete are one conditional statement in the repository, so a
        task filed between the two cannot be stranded on a plan id that no
        longer resolves.

        Args:
            existing: The plan being removed (already fetched by the caller).
            requested_by: Who asked, recorded on the audit event.

        Raises:
            PlanNotDeletableError: The plan is terminal, or work is still
                building under it.
            RecordNotFoundError: The plan went between the caller's fetch
                and this write. The audit line is the record that a plan
                was destroyed, so it may only follow a delete that found
                one; emitting it regardless would attest to a deletion
                that did not happen.
            QueryError: Repository write failure.
        """
        self._require_deletable_status(existing)
        outcome = await self._repo.delete_if_no_live_tasks(
            NotBlankStr(str(existing.id)),
            terminal_statuses=TERMINAL_TASK_STATUS_VALUES,
        )
        if outcome.live_task_count:
            logger.info(
                API_PLAN_DELETE_REFUSED,
                plan_id=str(existing.id),
                status=existing.status.value,
                live_task_count=outcome.live_task_count,
            )
            msg = (
                f"plan {existing.id} is {existing.status.value} and "
                f"{outcome.live_task_count} of its items are still building; "
                "replan it instead of deleting it"
            )
            raise PlanNotDeletableError(msg)
        if not outcome.deleted:
            msg = f"plan {existing.id} no longer exists"
            raise RecordNotFoundError(msg)
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
            ValidationError: FAILED was requested with no ``failure_reason``,
                or a reason was supplied alongside a live status.
            VersionConflictError: A concurrent write bumped the version first.
            RecordNotFoundError: The plan disappeared between fetch and write.
            QueryError: Repository write failure (logged before propagating).
        """
        self._require_legal_transition(existing, status)
        failing = status is PlanStatus.FAILED
        if failing and failure_reason is None:
            msg = "a plan may only be failed with a reason Plan Review can show"
            raise ValidationError(msg)
        # Rejected here rather than left to the entity: the live branch below
        # writes through ``model_copy``, which neither carries the reason nor
        # re-runs the validator that forbids it, so a caller pairing a reason
        # with a live status would otherwise have it silently dropped.
        if failure_reason is not None and not failing:
            msg = (
                "failure_reason is only valid for a FAILED plan, not "
                f"status={status.value}"
            )
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
        await self._log_transition(
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

    async def create(self, plan: Plan) -> None:
        """Persist a new plan and open its ledger at the status it was born at.

        The ledger answers "how did this plan get here", so the row it starts
        from is the status the plan first held. Callers that write a plan
        through the repository directly leave that first row missing, and the
        ledger then reads as complete while the story starts mid-sentence.

        Args:
            plan: The plan to create.

        Raises:
            DuplicateRecordError: A plan with this id already exists.
            QueryError: Repository write failure.
        """
        await self._repo.create(plan)
        await self._log_transition(None, plan)

    async def record_decomposed(self, decomposed: Plan, *, shell: Plan | None) -> None:
        """Persist a decomposed plan over its planning shell.

        The decomposition replaces the plan wholesale (items, review,
        provenance) AND moves its status, which is why it cannot go through
        :meth:`sync_status`. It still belongs here: the status half is a
        transition like any other, and the gate writing it straight to the
        repository is how the ledger came to record everything except the
        moment a plan actually acquired its items.

        Args:
            decomposed: The filled plan, already carrying its new version.
            shell: The planning shell it replaces, or ``None`` when the shell
                was lost (opened on a prior boot, then pruned) and the filled
                plan is persisted fresh.

        Raises:
            ConflictError: The shell's status cannot legally reach the
                decomposed plan's status.
            VersionConflictError: A concurrent write bumped the version first.
            RecordNotFoundError: The shell disappeared between fetch and write.
            QueryError: Repository write failure.
        """
        if shell is None:
            await self.create(decomposed)
            return
        self._require_legal_transition(shell, decomposed.status)
        await self._persist_update(
            decomposed,
            expected_version=shell.version,
            failure_event=API_PLAN_UPDATE_FAILED,
        )
        await self._log_transition(shell.status, decomposed)

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
        # A successor is a new plan entity, so its first status is a birth
        # like any other and needs the same durable row ``create`` writes.
        # Without it the ledger's account of how a replanned initiative
        # reached COMPLETED starts one revision too late.
        await self._log_transition(None, successor)
        logger.info(
            API_PLAN_SUCCESSOR_OPENED,
            plan_id=str(successor.id),
            supersedes=str(existing.id),
            item_count=len(successor.items),
        )
        return successor
