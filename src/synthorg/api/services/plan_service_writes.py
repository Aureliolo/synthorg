# module-kind: code
"""The two primitives every plan write funnels through.

Persisting a revision and recording the status change it produced are the
same two steps behind ``edit``, ``sync_status``, ``request_changes``,
``create`` and ``record_decomposed``, so they live once, here, rather than
being repeated per public method where one of them could quietly go missing:
a plan write recorded in one caller's path and not another's is how the
lifecycle ledger came to look complete without any single caller being
obviously wrong.

Split from :mod:`.plan_service` for the module-size budget; the mixin has no
state of its own and reads the service's repository and ledger.
"""

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import VersionConflictError
from synthorg.core.persistence_errors import PersistenceVersionConflictError
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_PLAN_STATUS_TRANSITIONED
from synthorg.persistence.lifecycle_ledger import LifecycleLedger
from synthorg.persistence.plan_protocol import PlanRepository

logger = get_logger(__name__)


class PlanWriteRecorderMixin:
    """Persist a plan revision and record the transition it produced.

    Mixed into :class:`~synthorg.api.services.plan_service.PlanService`,
    which supplies ``_repo`` and ``_ledger``.
    """

    __slots__ = ()

    _repo: PlanRepository
    _ledger: LifecycleLedger

    async def _log_transition(
        self,
        from_status: PlanStatus | None,
        plan: Plan,
        *,
        requested_by: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Record a plan status transition after the persistence write succeeds.

        The log line answers "what is happening now"; the ledger row answers
        "how did this plan get here", months later and from a query rather
        than a container's stdout.

        Args:
            from_status: The status the plan left, or ``None`` when the plan
                is being born and there is no prior status to leave.
            plan: The plan as persisted.
            requested_by: Identity driving the transition, when there is one.
            reason: Why the transition happened, when the caller has one.
        """
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
            from_status=from_status.value if from_status is not None else None,
            to_status=plan.status.value,
            version=plan.version,
            **context,
        )
        await self._ledger.record_plan(
            plan_id=plan.id,
            from_status=from_status,
            to_status=plan.status,
            entity_version=plan.version,
            requested_by=requested_by,
            reason=reason,
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


__all__ = ["PlanWriteRecorderMixin"]
