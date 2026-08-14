# module-kind: service
"""Checkpoint-resume steps for :class:`AgentEngine`.

The recovery mixin decides *whether* to resume and drives the resume; the
individual steps live here: establish the budget the resumed run answers to
and read its checkpoint, run the loop on a reconstituted context, then record
costs, apply the post-execution transitions and clear the checkpoint
artefacts.

Split from the recovery mixin so the decision and the steps have separate
homes; :class:`AgentEngineRecoveryMixin` inherits it, so the engine still sees
one flat surface.
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.engine.artifacts.expected_artifact_check import ExpectedArtifactProbe
from synthorg.engine.checkpoint.resume import (
    cleanup_checkpoint_artifacts,
)
from synthorg.engine.context import AgentContext
from synthorg.engine.cost_recording import record_execution_costs
from synthorg.engine.errors import RecoveryCheckpointMissingError
from synthorg.engine.loop_protocol import (
    BudgetChecker,
    ExecutionResult,
    TerminationReason,
    make_budget_checker,
)
from synthorg.engine.recovery import RecoveryResult, RecoveryStrategy
from synthorg.engine.task_sync import apply_post_execution_transitions
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_RESUME_COMPLETE,
    EXECUTION_RESUME_FAILED,
    EXECUTION_RESUME_START,
)
from synthorg.providers.models import CompletionConfig
from synthorg.providers.protocol import CompletionProvider

if TYPE_CHECKING:
    from synthorg.budget.enforcer import BudgetEnforcer
    from synthorg.budget.tracker_protocol import CostTrackerProtocol
    from synthorg.core.effective_autonomy import EffectiveAutonomy
    from synthorg.engine._agent_engine_callables import (
        MakeLoopWithCallback,
        MakeToolInvoker,
        ResolveLoop,
        ResolveMemoryStrategy,
        ValidateProject,
    )
    from synthorg.engine.loop_protocol import ExecutionLoop, ShutdownChecker
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.persistence.checkpoint_protocol import (
        CheckpointRepository,
        HeartbeatRepository,
    )
    from synthorg.persistence.project_protocol import ProjectRepository

logger = get_logger(__name__)


class ResumePreparation(BaseModel):
    """What a resume needs settled before the loop is reconstructed.

    The project id travels with the budget because validating the project is
    what establishes both: a resume under a project runs against that
    project's remaining budget, and reading one without the other would let
    the loop spend against a budget it was not admitted to.

    Attributes:
        checkpoint_json: Serialised checkpoint context to reconstruct from.
        project_id: Project the resumed run belongs to, if any.
        project_budget: Remaining project budget the resumed loop runs under.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    checkpoint_json: str = Field(description="Serialised checkpoint context")
    project_id: str | None = Field(default=None, description="Owning project id")
    project_budget: float = Field(
        default=0.0,
        ge=0.0,
        description="Remaining project budget for the resumed run",
    )


class AgentEngineCheckpointResumeMixin:
    """Mixin performing a checkpoint resume once recovery has chosen one."""

    _recovery_strategy: RecoveryStrategy | None
    _project_repo: ProjectRepository | None
    _validate_project: ValidateProject
    _budget_enforcer: BudgetEnforcer | None
    _loop: ExecutionLoop
    _resolve_loop: ResolveLoop
    _make_loop_with_callback: MakeLoopWithCallback
    _provider: CompletionProvider
    _make_tool_invoker: MakeToolInvoker
    _resolve_memory_strategy: ResolveMemoryStrategy
    _shutdown_checker: ShutdownChecker | None
    _cost_tracker: CostTrackerProtocol | None
    _task_engine: TaskEngine | None
    _artifact_probe: ExpectedArtifactProbe | None
    _approval_store: ApprovalStoreProtocol | None
    _checkpoint_repo: CheckpointRepository | None
    _heartbeat_repo: HeartbeatRepository | None

    def _validate_checkpoint_json(
        self,
        recovery_result: RecoveryResult,
        agent_id: str,
        task_id: str,
    ) -> str:
        """Return checkpoint JSON or raise if unexpectedly absent.

        Returns:
            The serialised checkpoint context JSON string from
            ``recovery_result``.

        Raises:
            RecoveryCheckpointMissingError: If ``checkpoint_context_json``
                is ``None`` despite the strategy reporting ``can_resume``
                true.
        """
        if recovery_result.checkpoint_context_json is None:
            logger.error(
                EXECUTION_RESUME_FAILED,
                agent_id=agent_id,
                task_id=task_id,
                error="checkpoint_context_json is None but can_resume was True",
            )
            msg = "checkpoint_context_json is None but can_resume was True"
            raise RecoveryCheckpointMissingError(msg)
        return recovery_result.checkpoint_context_json

    async def _prepare_resume(
        self,
        recovery_result: RecoveryResult,
        task: Task,
        agent_id: str,
        task_id: str,
        *,
        project_id: str | None,
    ) -> ResumePreparation:
        """Establish the project budget and checkpoint a resume needs.

        Returns:
            The validated checkpoint JSON plus the project id and budget the
            resumed loop runs under.
        """
        project_budget = 0.0
        if self._project_repo is not None:
            project_budget = await self._validate_project(
                task=task,
                agent_id=agent_id,
                task_id=task_id,
            )
            project_id = task.project
        checkpoint_json = self._validate_checkpoint_json(
            recovery_result,
            agent_id,
            task_id,
        )
        logger.info(
            EXECUTION_RESUME_START,
            agent_id=agent_id,
            task_id=task_id,
            resume_attempt=recovery_result.resume_attempt,
        )
        return ResumePreparation(
            checkpoint_json=checkpoint_json,
            project_id=project_id,
            project_budget=project_budget,
        )

    async def _execute_resumed_loop(
        self,
        checkpoint_ctx: AgentContext,
        agent_id: str,
        task_id: str,
        *,
        completion_config: CompletionConfig | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
        provider: CompletionProvider | None = None,
        project_id: str | None = None,
        project_budget: float = 0.0,
    ) -> ExecutionResult:
        """Run the execution loop on a reconstituted checkpoint context.

        Returns:
            The :class:`ExecutionResult` from running the engine's
            configured loop against the reconciled checkpoint context.
        """
        budget_checker: BudgetChecker | None
        if checkpoint_ctx.task_execution is None:
            budget_checker = None
        elif self._budget_enforcer:
            budget_checker = await self._budget_enforcer.make_budget_checker(
                checkpoint_ctx.task_execution.task,
                agent_id,
                project_id=project_id,
                project_budget=project_budget,
            )
        else:
            budget_checker = make_budget_checker(
                checkpoint_ctx.task_execution.task,
            )

        base_loop = self._loop
        if checkpoint_ctx.task_execution is not None:
            base_loop = await self._resolve_loop(
                checkpoint_ctx.task_execution.task,
                agent_id,
                task_id,
            )
        loop = self._make_loop_with_callback(base_loop, agent_id, task_id)
        result: ExecutionResult = await loop.execute(
            context=checkpoint_ctx,
            provider=provider or self._provider,
            tool_invoker=self._make_tool_invoker(
                checkpoint_ctx.identity,
                task_id=task_id,
                effective_autonomy=effective_autonomy,
                project_id=project_id,
                memory_strategy=self._resolve_memory_strategy(),
            ),
            budget_checker=budget_checker,
            shutdown_checker=self._shutdown_checker,
            completion_config=completion_config,
        )
        return result

    async def _finalize_resume(
        self,
        result: ExecutionResult,
        identity: AgentIdentity,
        execution_id: str,
        agent_id: str,
        task_id: str,
        *,
        project_id: str | None = None,
    ) -> ExecutionResult:
        """Record costs, apply transitions, and clean up after resume.

        Returns:
            The :class:`ExecutionResult` with post-execution task-state
            transitions applied; checkpoint artefacts are cleaned up
            when the resumed run did not terminate with ``ERROR``.

        Raises:
            ExecutionStateError: When a post-execution transition cannot
                land. The cleanup still runs first: a task whose state
                could not be moved must not also leave its checkpoint and
                heartbeat rows behind for a resume that will never come.
        """
        await record_execution_costs(
            result,
            identity,
            agent_id,
            task_id,
            tracker=self._cost_tracker,
            project_id=project_id,
        )
        try:
            result = await apply_post_execution_transitions(
                result,
                agent_id=agent_id,
                task_id=task_id,
                task_engine=self._task_engine,
                approval_store=self._approval_store,
                artifact_probe=self._artifact_probe,
            )
            logger.info(
                EXECUTION_RESUME_COMPLETE,
                agent_id=agent_id,
                task_id=task_id,
                termination_reason=result.termination_reason.value,
            )
        finally:
            if result.termination_reason != TerminationReason.ERROR:
                if self._recovery_strategy is not None:
                    await self._recovery_strategy.finalize(execution_id)
                await cleanup_checkpoint_artifacts(
                    self._checkpoint_repo,
                    self._heartbeat_repo,
                    execution_id,
                )
        return result
