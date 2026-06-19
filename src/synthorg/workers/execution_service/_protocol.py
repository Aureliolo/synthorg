# module-kind: code
"""Worker-callable execution-surface protocol."""

from typing import Protocol, runtime_checkable

from synthorg.core.task import (
    Task,
)


@runtime_checkable
class WorkerExecutionService(Protocol):
    """Contract for the worker-callable execution surface.

    The wired implementation is selected by the runtime builder behind
    the provider-present switch (see :mod:`synthorg.workers.runtime_builder`):
    :class:`AgentEngineExecutionService` when a provider is configured,
    :class:`NoProviderExecutionService` otherwise.
    :class:`LifecycleAdvancingExecutionService` is the lifecycle-only
    baseline the dispatcher / queue / worker integration tests pin and
    the property's lazy fallback when no explicit service is installed.
    """

    async def execute_once(
        self,
        *,
        task_id: str,
        previous_status: str | None,
        new_status: str,
        idempotency_key: str,
        requested_by: str,
    ) -> Task:
        """Execute one step of the task and return the post-step state.

        Implementations MUST persist the resulting status through the
        ``TaskEngine`` so the single-writer invariant holds, and
        return the typed ``Task`` for the controller to envelope.
        """
        ...

    async def dispatch_resume(
        self,
        *,
        approval_id: str,
        approved: bool,
        decided_by: str,
        decision_reason: str | None,
    ) -> None:
        """Schedule a parked-context resume off the request path.

        Called by the ``/approvals`` controller once a decision is
        persisted and a parked context is known to exist. The agent
        runtime implementation restores the parked ``AgentContext`` via
        the shared ``ApprovalGate``, injects the decision, and
        continues the original run as a tracked background task,
        returning immediately so the approve/reject HTTP response is
        not blocked by a full agent re-run. Non-runtime implementations
        reject loudly: a parked context with no agent engine to resume
        it is a misconfiguration, not a no-op.
        """
        ...
