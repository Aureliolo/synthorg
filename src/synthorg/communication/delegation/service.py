"""Delegation service orchestrating hierarchy, authority, and loop prevention."""

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError

from synthorg.communication.delegation.authority import (
    AuthorityValidator,
)
from synthorg.communication.delegation.entity_guard import (
    EntityAlignmentGuard,
)
from synthorg.communication.delegation.hierarchy import (
    HierarchyResolver,
)
from synthorg.communication.delegation.models import (
    DelegationRecord,
    DelegationRequest,
    DelegationResult,
)
from synthorg.communication.delegation.record_store import (
    DelegationRecordStore,
)
from synthorg.communication.errors import DelegationError
from synthorg.communication.loop_prevention.guard import (
    DelegationGuard,
)
from synthorg.core.agent import AgentIdentity
from synthorg.core.concurrency.refcounted_lock_map import RefcountedLockMap
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.delegation import (
    DELEGATION_CREATED,
    DELEGATION_LOOP_ESCALATED,
    DELEGATION_RECORD_STORE_FAILED,
    DELEGATION_REQUEST_INVALID,
    DELEGATION_REQUESTED,
    DELEGATION_RESULT_SENT,
    DELEGATION_SUB_TASK_FAILED,
)

logger = get_logger(__name__)


class DelegationService:
    """Orchestrates hierarchical delegation with loop prevention.

    Validates authority, checks loop prevention guards, creates
    sub-tasks, and records audit trail entries. The core logic is
    synchronous (CPU-only); messaging is a separate async concern.

    Args:
        hierarchy: Resolved organizational hierarchy.
        authority_validator: Authority validation logic.
        guard: Loop prevention guard.
        record_store: Optional delegation record store for activity tracking.
    """

    __slots__ = (
        "_audit_trail",
        "_authority_validator",
        "_delegate_locks",
        "_entity_guard",
        "_guard",
        "_hierarchy",
        "_record_store",
    )

    def __init__(
        self,
        *,
        hierarchy: HierarchyResolver,
        authority_validator: AuthorityValidator,
        guard: DelegationGuard,
        record_store: DelegationRecordStore | None = None,
        entity_guard: EntityAlignmentGuard | None = None,
    ) -> None:
        self._hierarchy = hierarchy
        self._authority_validator = authority_validator
        self._guard = guard
        self._record_store = record_store
        self._entity_guard = entity_guard
        self._audit_trail: list[DelegationRecord] = []
        # Serialises the loop-prevention check -> async entity guard ->
        # record window so two concurrent ``delegate`` calls for the same
        # pair cannot both pass the rate-limit / dedup ``check`` (sync,
        # in-memory) before either ``record``s, blowing past the limit.
        # A per-pair keyed lock (not a single global lock) keeps unrelated
        # delegation pairs concurrent across the entity-guard await. The
        # key is direction-agnostic (sorted) so A->B and B->A serialise
        # together, matching the circuit breaker's direction-agnostic
        # bounce counter -- a direction-sensitive key would let opposite
        # directions race on that shared counter.
        self._delegate_locks: RefcountedLockMap[str] = RefcountedLockMap()

    async def delegate(
        self,
        request: DelegationRequest,
        delegator: AgentIdentity,
        delegatee: AgentIdentity,
    ) -> DelegationResult:
        """Execute a delegation: authority, loops, sub-task, audit.

        Args:
            request: The delegation request.
            delegator: Identity of the delegating agent.
            delegatee: Identity of the target agent.

        Returns:
            Result indicating success or rejection with reason.

        Raises:
            ValueError: If request IDs do not match identity objects.
            DelegationError: If sub-task construction fails.
        """
        self._validate_identity(request, delegator, delegatee)

        logger.info(
            DELEGATION_REQUESTED,
            delegator=request.delegator_id,
            delegatee=request.delegatee_id,
            task_id=str(request.task.id),
        )

        # 1. Authority check
        auth_result = self._authority_validator.validate(delegator, delegatee)
        if not auth_result.allowed:
            return DelegationResult(
                success=False,
                rejection_reason=auth_result.reason,
                blocked_by="authority",
            )

        # Steps 2-4 form one check-and-record critical section over the
        # in-process guard + audit-trail state. Serialise it per pair so
        # concurrent delegations for the same pair cannot interleave
        # between the sync ``check`` and the ``record`` (the await on the
        # entity guard in step 3 is the interleaving window the lock
        # closes), while unrelated pairs proceed concurrently.
        pair_key = ":".join(sorted((request.delegator_id, request.delegatee_id)))
        async with self._delegate_locks.acquire(pair_key):
            # 2. Loop prevention checks
            guard_outcome = self._guard.check(
                delegation_chain=request.task.delegation_chain,
                delegator_id=request.delegator_id,
                delegatee_id=request.delegatee_id,
                task_id=str(request.task.id),
            )
            if not guard_outcome.passed:
                self._escalate_loop_detection(request, guard_outcome.mechanism)
                return DelegationResult(
                    success=False,
                    rejection_reason=guard_outcome.message,
                    blocked_by=guard_outcome.mechanism,
                )

            # 3. Entity alignment guard (async)
            entity_versions: Mapping[str, int] | None = None
            if self._entity_guard is not None:
                guard_result = await self._entity_guard.check(request)
                entity_versions = guard_result.entity_versions
                if not guard_result.passed:
                    return DelegationResult(
                        success=False,
                        rejection_reason=guard_result.message,
                        blocked_by=guard_result.mechanism,
                    )

            # 4. Create sub-task and record
            sub_task = self._create_sub_task(request)
            self._record_delegation(
                request,
                sub_task,
                entity_versions=entity_versions,
            )

        return DelegationResult(success=True, delegated_task=sub_task)

    @staticmethod
    def reject_delegated_task(task: Task) -> Task:
        """Transition a CREATED task to REJECTED.

        Used when a delegatee explicitly refuses a task that was
        already created for them. The task must be in CREATED status.

        Args:
            task: The task to reject (must be in CREATED status).

        Returns:
            A new Task in REJECTED status.

        Raises:
            ValueError: If the task is not in CREATED status.
        """
        return task.with_transition(TaskStatus.REJECTED)

    @staticmethod
    def _validate_identity(
        request: DelegationRequest,
        delegator: AgentIdentity,
        delegatee: AgentIdentity,
    ) -> None:
        """Verify request IDs match the identity objects.

        Args:
            request: The delegation request.
            delegator: Identity of the delegating agent.
            delegatee: Identity of the target agent.

        Raises:
            ValueError: If IDs do not match.
        """
        if request.delegator_id != delegator.name:
            msg = (
                f"request.delegator_id {request.delegator_id!r} does not "
                f"match delegator.name {delegator.name!r}"
            )
            logger.warning(
                DELEGATION_REQUEST_INVALID,
                reason="delegator_id_mismatch",
                error_type=ValueError.__name__,
            )
            raise ValueError(msg)
        if request.delegatee_id != delegatee.name:
            msg = (
                f"request.delegatee_id {request.delegatee_id!r} does not "
                f"match delegatee.name {delegatee.name!r}"
            )
            logger.warning(
                DELEGATION_REQUEST_INVALID,
                reason="delegatee_id_mismatch",
                error_type=ValueError.__name__,
            )
            raise ValueError(msg)

    def _record_delegation(
        self,
        request: DelegationRequest,
        sub_task: Task,
        *,
        entity_versions: Mapping[str, int] | None = None,
    ) -> None:
        """Record delegation in guard state and audit trail.

        Args:
            request: The delegation request.
            sub_task: The created sub-task.
            entity_versions: Entity version manifest at delegation time.
        """
        self._guard.record_delegation(
            request.delegator_id,
            request.delegatee_id,
            str(request.task.id),
        )
        record = DelegationRecord(
            delegation_id=str(uuid4()),
            delegator_id=request.delegator_id,
            delegatee_id=request.delegatee_id,
            original_task_id=str(request.task.id),
            delegated_task_id=str(sub_task.id),
            timestamp=datetime.now(UTC),
            refinement=request.refinement,
            entity_versions=entity_versions,
        )
        self._audit_trail.append(record)
        if self._record_store is not None:
            try:
                self._record_store.append(record)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    DELEGATION_RECORD_STORE_FAILED,
                    delegator=request.delegator_id,
                    delegatee=request.delegatee_id,
                )

        logger.info(
            DELEGATION_CREATED,
            delegator=request.delegator_id,
            delegatee=request.delegatee_id,
            original_task_id=str(request.task.id),
            delegated_task_id=str(sub_task.id),
        )
        logger.debug(
            DELEGATION_RESULT_SENT,
            delegator=request.delegator_id,
            delegatee=request.delegatee_id,
            success=True,
        )

    def _create_sub_task(self, request: DelegationRequest) -> Task:
        """Create a new sub-task from a delegation request.

        The sub-task inherits the original task's properties but gets
        a new ID, parent reference, extended delegation chain, and
        CREATED status.  Constraints and refinement are appended to
        the description so the delegatee receives full context.

        Args:
            request: The delegation request.

        Returns:
            New Task with delegation metadata.

        Raises:
            DelegationError: If Task construction fails.
        """
        original = request.task
        new_chain = (*original.delegation_chain, request.delegator_id)
        description = original.description
        if request.refinement:
            description = f"{description}\n\nDelegation context: {request.refinement}"
        if request.constraints:
            constraints_text = "\n".join(f"- {c}" for c in request.constraints)
            description = f"{description}\n\nConstraints:\n{constraints_text}"

        try:
            return Task(
                id=uuid4(),
                title=original.title,
                description=description,
                type=original.type,
                priority=original.priority,
                project=original.project,
                created_by=request.delegator_id,
                parent_task_id=str(original.id),
                delegation_chain=new_chain,
                estimated_complexity=original.estimated_complexity,
                budget_limit=original.budget_limit,
                deadline=original.deadline,
                max_retries=original.max_retries,
                reviewers=original.reviewers,
                dependencies=original.dependencies,
                artifacts_expected=original.artifacts_expected,
                acceptance_criteria=original.acceptance_criteria,
            )
        except ValidationError as exc:
            logger.warning(
                DELEGATION_SUB_TASK_FAILED,
                delegator=request.delegator_id,
                delegatee=request.delegatee_id,
                original_task_id=original.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = (
                f"Failed to create sub-task for delegation "
                f"from {request.delegator_id!r} to "
                f"{request.delegatee_id!r}"
            )
            raise DelegationError(
                msg,
                context={
                    "delegator_id": request.delegator_id,
                    "delegatee_id": request.delegatee_id,
                    "original_task_id": original.id,
                },
            ) from exc

    def _escalate_loop_detection(
        self,
        request: DelegationRequest,
        mechanism: str,
    ) -> None:
        """Log escalation when a loop prevention mechanism blocks delegation.

        Looks up the delegator's supervisor and logs the event so that
        the supervisor can be notified (notification delivery is an
        async concern handled elsewhere).

        Args:
            request: The blocked delegation request.
            mechanism: Name of the mechanism that blocked.
        """
        supervisor = self._hierarchy.get_supervisor(request.delegator_id)
        logger.warning(
            DELEGATION_LOOP_ESCALATED,
            delegator=request.delegator_id,
            delegatee=request.delegatee_id,
            task_id=str(request.task.id),
            mechanism=mechanism,
            supervisor=supervisor,
        )

    def get_audit_trail(self) -> tuple[DelegationRecord, ...]:
        """Return all delegation audit records.

        Returns:
            Tuple of delegation records in chronological order.
        """
        return tuple(self._audit_trail)

    def get_supervisor_of(self, agent_name: str) -> str | None:
        """Expose hierarchy lookup for escalation callers.

        Args:
            agent_name: Agent name to look up.

        Returns:
            Supervisor name or None if at the top.
        """
        return self._hierarchy.get_supervisor(agent_name)
