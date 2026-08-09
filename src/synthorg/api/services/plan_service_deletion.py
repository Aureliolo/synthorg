# module-kind: code
"""What may be removed, and when.

A plan is either a request that never became work, which an operator may
clear, or a record of what was decided, which outlives the decision to stop
pursuing it. Deciding between those is one question with three inputs (the
plan's status, whether the project it is a record about still exists, and
whether work is still building under it), so it lives here rather than being
re-derived at each of the two delete routes.

Split from :mod:`.plan_service` for the module-size budget; the mixin has no
state of its own and reads the service's repositories.
"""

from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import PlanNotDeletableError
from synthorg.core.persistence_errors import RecordNotFoundError
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import (
    DELETABLE_STATUSES,
    REPLANNABLE_STATUSES,
    TAIL_STATUSES,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.task_engine_apply_helpers import TRULY_TERMINAL_STATUSES
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_PLAN_DELETE_REFUSED,
    API_PLAN_DELETED,
    API_PLAN_FETCH_FAILED,
)
from synthorg.persistence.plan_protocol import PlanRepository
from synthorg.persistence.project_protocol import ProjectRepository

logger = get_logger(__name__)

#: Task status values the guarded delete reads as finished, derived from the
#: engine's terminal set rather than restated, so a new terminal status cannot
#: be missed here. Rendered to the persisted wire values because the guard runs
#: as SQL against the status column.
TERMINAL_TASK_STATUS_VALUES: Final[frozenset[str]] = frozenset(
    status.value for status in TRULY_TERMINAL_STATUSES
)


class PlanDeletionMixin:
    """Both plan-delete routes and the policy they share.

    Mixed into :class:`~synthorg.api.services.plan_service.PlanService`,
    which supplies ``_repo`` and ``_projects``.
    """

    __slots__ = ()

    _repo: PlanRepository
    _projects: ProjectRepository

    @staticmethod
    def _require_deletable_status(plan: Plan) -> None:
        """Refuse a delete on a status that is a record, not a request.

        A terminal plan is what was decided, and its delivery verdicts hang
        off the row, so it outlives the decision to stop pursuing it. Every
        other status may be deleted subject to the live-work guard, which is
        the database's answer rather than this one.

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

    async def _subject_exists(self, plan: Plan) -> bool:
        """Whether the project this plan is a record *about* still exists.

        A decided plan is retained because it records what was decided, and a
        record is about a subject. Once the project is gone the record answers
        nothing, and the only route that may remove a decided plan is the
        project's own teardown, which can never run again. A live run left two
        SUPERSEDED plans naming deleted projects: unreachable by every route,
        and holding their objective tasks undeletable with them.

        A read failure answers ``True``. The alternative would let a transient
        outage open the delete on a plan whose project is sitting there, which
        is the one case this must not do.

        Returns:
            Whether the project resolves.
        """
        try:
            return await self._projects.get(NotBlankStr(str(plan.project))) is not None
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                API_PLAN_FETCH_FAILED,
                plan_id=str(plan.id),
                project=str(plan.project),
                reason="project_lookup_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return True

    async def delete(self, existing: Plan, *, requested_by: str) -> None:
        """Remove a request that never became work.

        The route exists to clear a plan an operator has decided not to
        pursue: a shell whose decomposition stranded, a draft, one waiting
        on review, one that failed, or a dispatched one whose tasks never
        made it onto the board. A decided plan is refused while its project
        is there to read it, and so is any plan with work still building
        under it. The refusal routes through here rather than the controller
        so the one irreversible plan operation is audited on the same path as
        every reversible one.

        Live work is not counted here and then deleted afterwards: the count
        and the delete are one conditional statement in the repository, so a
        task filed between the two cannot be stranded on a plan id that no
        longer resolves.

        Args:
            existing: The plan being removed (already fetched by the caller).
            requested_by: Who asked, recorded on the audit event.

        Raises:
            PlanNotDeletableError: The plan is decided and its project is
                still there to read it, or work is still building under it.
            RecordNotFoundError: The plan went between the caller's fetch
                and this write. The audit line is the record that a plan
                was destroyed, so it may only follow a delete that found
                one; emitting it regardless would attest to a deletion
                that did not happen.
            QueryError: Repository write failure.
        """
        if await self._subject_exists(existing):
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

    async def delete_for_project_teardown(self, existing: Plan) -> bool:
        """Remove a plan because the project it belongs to is being deleted.

        The per-resource refusal in :meth:`delete` protects a *decided*
        plan from an operator removing it on its own: its verdicts are the
        record of what was decided. That record is about a subject. Once
        the operator deletes the project, the subject is gone, and keeping
        a SUPERSEDED plan that names a 404 preserves nothing readable while
        making the row unreachable and unremovable by any route.

        A live run proved the shape: project delete returned 204, retired
        each plan, and left two SUPERSEDED plans and their objective tasks
        permanently in the system, each pointing at a project that no
        longer resolved. Which children survived depended on nothing more
        principled than whether the plan had items, because that is what
        chooses SUPERSEDED over FAILED.

        The live-work guard still applies: it is one conditional statement
        in the repository, so a task filed between the check and the delete
        cannot be stranded.

        Returns:
            ``True`` when the plan was removed, ``False`` when live work
            under it refused the delete or it had already gone.
        """
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
                reason="live_tasks_during_project_teardown",
            )
            return False
        if outcome.deleted:
            logger.info(
                API_PLAN_DELETED,
                plan_id=str(existing.id),
                status=existing.status.value,
                requested_by="project-teardown",
            )
        return outcome.deleted


__all__ = ["TERMINAL_TASK_STATUS_VALUES", "PlanDeletionMixin"]
