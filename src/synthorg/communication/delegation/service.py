"""Delegation service orchestrating hierarchy, authority, and loop prevention."""

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError

from synthorg.communication.delegation.authority import (  # noqa: TC001
    AuthorityValidator,
)
from synthorg.communication.delegation.hierarchy import (  # noqa: TC001
    HierarchyResolver,
)
from synthorg.communication.delegation.models import (
    DelegationRecord,
    DelegationRequest,
    DelegationResult,
)
from synthorg.communication.errors import DelegationError
from synthorg.communication.loop_prevention.guard import (  # noqa: TC001
    DelegationGuard,
)
from synthorg.core.agent import AgentIdentity  # noqa: TC001
from synthorg.core.task import Task
from synthorg.observability import get_logger
from synthorg.observability.events.delegation import (
    DELEGATION_CREATED,
    DELEGATION_LOOP_ESCALATED,
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
    """

    __slots__ = (
        "_audit_trail",
        "_authority_validator",
        "_guard",
        "_hierarchy",
    )

    def __init__(
        self,
        *,
        hierarchy: HierarchyResolver,
        authority_validator: AuthorityValidator,
        guard: DelegationGuard,
    ) -> None:
        self._hierarchy = hierarchy
        self._authority_validator = authority_validator
        self._guard = guard
        self._audit_trail: list[DelegationRecord] = []

    def delegate(
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
            task_id=request.task.id,
        )

        # 1. Authority check
        auth_result = self._authority_validator.validate(delegator, delegatee)
        if not auth_result.allowed:
            return DelegationResult(
                success=False,
                rejection_reason=auth_result.reason,
                blocked_by="authority",
            )

        # 2. Loop prevention checks
        guard_outcome = self._guard.check(
            delegation_chain=request.task.delegation_chain,
            delegator_id=request.delegator_id,
            delegatee_id=request.delegatee_id,
            task_id=request.task.id,
        )
        if not guard_outcome.passed:
            self._escalate_loop_detection(request, guard_outcome.mechanism)
            return DelegationResult(
                success=False,
                rejection_reason=guard_outcome.message,
                blocked_by=guard_outcome.mechanism,
            )

        # 3. Create sub-task and record
        sub_task = self._create_sub_task(request)
        self._record_delegation(request, sub_task)

        return DelegationResult(success=True, delegated_task=sub_task)

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
            raise ValueError(msg)
        if request.delegatee_id != delegatee.name:
            msg = (
                f"request.delegatee_id {request.delegatee_id!r} does not "
                f"match delegatee.name {delegatee.name!r}"
            )
            raise ValueError(msg)

    def _record_delegation(
        self,
        request: DelegationRequest,
        sub_task: Task,
    ) -> None:
        """Record delegation in guard state and audit trail.

        Args:
            request: The delegation request.
            sub_task: The created sub-task.
        """
        self._guard.record_delegation(
            request.delegator_id,
            request.delegatee_id,
            request.task.id,
        )
        record = DelegationRecord(
            delegation_id=str(uuid4()),
            delegator_id=request.delegator_id,
            delegatee_id=request.delegatee_id,
            original_task_id=request.task.id,
            delegated_task_id=sub_task.id,
            timestamp=datetime.now(UTC),
            refinement=request.refinement,
        )
        self._audit_trail.append(record)

        logger.info(
            DELEGATION_CREATED,
            delegator=request.delegator_id,
            delegatee=request.delegatee_id,
            original_task_id=request.task.id,
            delegated_task_id=sub_task.id,
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
                id=f"del-{uuid4().hex[:12]}",
                title=original.title,
                description=description,
                type=original.type,
                priority=original.priority,
                project=original.project,
                created_by=request.delegator_id,
                parent_task_id=original.id,
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
            logger.exception(
                DELEGATION_SUB_TASK_FAILED,
                delegator=request.delegator_id,
                delegatee=request.delegatee_id,
                original_task_id=original.id,
                error=str(exc),
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
            task_id=request.task.id,
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
