# module-kind: service
"""Empty-company worker execution backstop (no provider)."""

from synthorg.approval.resume_annotations import (
    DEFAULT_RESUME_ANNOTATIONS,
    ResumeAnnotations,
)
from synthorg.core.domain_errors import (
    AgentRuntimeNotConfiguredError,
)
from synthorg.core.task import (
    Task,
)
from synthorg.engine.pipeline.models import WorkItem
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.observability import (
    get_logger,
)
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_RESUME_FAILED,
)
from synthorg.observability.events.workers import (
    WORKERS_EXECUTION_SERVICE_NO_PROVIDER,
)

logger = get_logger(__name__)


class NoProviderExecutionService:
    """Empty-company :class:`WorkerExecutionService`.

    Installed when no LLM provider is configured. Task creation is
    already rejected at the submission boundary; this is the
    defence-in-depth backstop so a task that reaches the execute seam
    by any other path fails loudly instead of running ungoverned or
    silently walking status labels.
    """

    __slots__ = ()

    async def execute_once(
        self,
        *,
        task_id: str,
        previous_status: str | None,
        new_status: str,
        idempotency_key: str,
        requested_by: str,
    ) -> Task:
        """Reject execution: the company has no provider configured.

        Raises:
            AgentRuntimeNotConfiguredError: Always; no LLM provider is
                configured (empty-company mode).
        """
        logger.warning(
            WORKERS_EXECUTION_SERVICE_NO_PROVIDER,
            task_id=task_id,
            previous_status=previous_status,
            new_status=new_status,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
        )
        msg = (
            "No LLM provider is configured; the company is running in "
            "empty mode and cannot execute tasks. Add a provider in "
            "setup, then resubmit."
        )
        raise AgentRuntimeNotConfiguredError(msg)

    async def dispatch_resume(
        self,
        *,
        approval_id: str,
        approved: bool,
        decided_by: str,
        decision_reason: str | None,
        annotations: ResumeAnnotations = DEFAULT_RESUME_ANNOTATIONS,
    ) -> None:
        """Reject: no provider means no agent engine to resume into.

        A parked context implies an ``AgentEngine`` ran before the
        provider was removed; surfacing this loudly tells the operator
        the deployment is misconfigured rather than silently dropping
        an approved resume.

        Raises:
            AgentRuntimeNotConfiguredError: Always; no provider means no
                agent engine to resume into.
        """
        logger.error(
            APPROVAL_GATE_RESUME_FAILED,
            approval_id=approval_id,
            approved=approved,
            decided_by=decided_by,
            has_reason=decision_reason is not None,
            reason_provenance=annotations.reason_provenance.value,
            has_system_note=annotations.system_note is not None,
            reason="no_provider_cannot_resume_agent",
        )
        msg = (
            f"Approval {approval_id!r} has a parked agent context but no "
            f"LLM provider is configured; cannot resume execution. "
            f"Restore the provider, then retry the decision."
        )
        raise AgentRuntimeNotConfiguredError(msg)

    def dispatch_conversational_execution(
        self,
        *,
        work_pipeline: WorkPipeline,
        work_item: WorkItem,
        task: Task,
    ) -> None:
        """Reject: no provider means no agent engine to run the work.

        Raises:
            AgentRuntimeNotConfiguredError: Always; no provider means no
                agent engine to execute the approved work.
        """
        logger.error(
            WORKERS_EXECUTION_SERVICE_NO_PROVIDER,
            task_id=str(task.id),
            source=work_item.source.value,
            pipeline=type(work_pipeline).__name__,
            reason="no_provider_cannot_execute_conversational_work",
        )
        msg = (
            f"Task {task.id!s} was intake-created for approved conversational "
            f"work but no LLM provider is configured; cannot execute. Restore "
            f"the provider, then retry."
        )
        raise AgentRuntimeNotConfiguredError(msg)
