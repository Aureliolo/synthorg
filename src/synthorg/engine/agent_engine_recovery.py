"""Recovery and checkpoint-resume mixin for :class:`AgentEngine`."""

from typing import TYPE_CHECKING

from synthorg.budget.errors import BudgetExhaustedError
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.engine.checkpoint.resume import (
    cleanup_checkpoint_artifacts,
    deserialize_and_reconcile,
)
from synthorg.engine.context import AgentContext
from synthorg.engine.cost_recording import record_execution_costs
from synthorg.engine.errors import (
    ProjectAgentNotMemberError,
    ProjectNotFoundError,
)
from synthorg.engine.loop_protocol import (
    BudgetChecker,
    ExecutionResult,
    TerminationReason,
    make_budget_checker,
)
from synthorg.engine.recovery import (
    FailureCategory,
    RecoveryResult,
    RecoveryStrategy,
)
from synthorg.engine.task_sync import apply_post_execution_transitions
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_RECOVERY_FAILED,
    EXECUTION_RESUME_COMPLETE,
    EXECUTION_RESUME_FAILED,
    EXECUTION_RESUME_START,
)
from synthorg.providers.models import CompletionConfig
from synthorg.providers.protocol import CompletionProvider

if TYPE_CHECKING:
    from synthorg.budget.enforcer import BudgetEnforcer
    from synthorg.budget.tracker import CostTracker
    from synthorg.engine._agent_engine_callables import (
        MakeLoopWithCallback,
        MakeToolInvoker,
        ResolveLoop,
        ValidateProject,
    )
    from synthorg.engine.loop_protocol import ExecutionLoop, ShutdownChecker
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.persistence.checkpoint_protocol import (
        CheckpointRepository,
        HeartbeatRepository,
    )
    from synthorg.persistence.project_protocol import ProjectRepository
    from synthorg.security.autonomy.models import EffectiveAutonomy

logger = get_logger(__name__)


class AgentEngineRecoveryMixin:
    """Mixin providing recovery and checkpoint-resume helpers."""

    _recovery_strategy: RecoveryStrategy | None
    _project_repo: ProjectRepository | None
    _validate_project: ValidateProject
    _budget_enforcer: BudgetEnforcer | None
    _loop: ExecutionLoop
    _resolve_loop: ResolveLoop
    _make_loop_with_callback: MakeLoopWithCallback
    _provider: CompletionProvider
    _make_tool_invoker: MakeToolInvoker
    _shutdown_checker: ShutdownChecker | None
    _cost_tracker: CostTracker | None
    _task_engine: TaskEngine | None
    _checkpoint_repo: CheckpointRepository | None
    _heartbeat_repo: HeartbeatRepository | None

    async def _apply_recovery(  # noqa: PLR0913
        self,
        execution_result: ExecutionResult,
        identity: AgentIdentity,
        agent_id: str,
        task_id: str,
        *,
        completion_config: CompletionConfig | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
        provider: CompletionProvider | None = None,
        project_id: str | None = None,
    ) -> tuple[ExecutionResult, RecoveryResult | None]:
        """Invoke the configured recovery strategy on error outcomes.

        Returns:
            ``(execution_result, recovery_result)`` where
            ``execution_result`` is the (possibly resumed) execution
            and ``recovery_result`` is the strategy's decision (``None``
            when no strategy is wired or recovery raised a non-typed
            error that was logged and swallowed).

        Raises:
            ProjectNotFoundError: Re-raised from the strategy when the
                project context is gone.
            ProjectAgentNotMemberError: Re-raised from the strategy
                when the agent is no longer a project member.
            BudgetExhaustedError: Re-raised from the strategy when
                resume cost would exceed the remaining budget.
        """
        if self._recovery_strategy is None:
            return execution_result, None
        ctx = execution_result.context
        if ctx.task_execution is None:
            return execution_result, None

        error_msg = execution_result.error_message or "Unknown error"
        try:
            recovery_result = await self._recovery_strategy.recover(
                task_execution=ctx.task_execution,
                error_message=error_msg,
                context=ctx,
            )

            if recovery_result.can_resume:
                resumed = await self._resume_from_checkpoint(
                    recovery_result,
                    identity,
                    ctx.task_execution.task,
                    agent_id,
                    task_id,
                    completion_config=completion_config,
                    effective_autonomy=effective_autonomy,
                    provider=provider,
                    project_id=project_id,
                )
                return resumed, recovery_result

            updated_ctx = ctx.model_copy(
                update={"task_execution": recovery_result.task_execution},
            )
            updated_result = execution_result.model_copy(
                update={"context": updated_ctx},
            )
            return updated_result, recovery_result  # noqa: TRY300
        except ProjectNotFoundError, ProjectAgentNotMemberError:
            raise
        except BudgetExhaustedError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                EXECUTION_RECOVERY_FAILED,
                agent_id=agent_id,
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return execution_result, None

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
            RuntimeError: If ``checkpoint_context_json`` is ``None``
                despite the strategy reporting ``can_resume`` true.
        """
        if recovery_result.checkpoint_context_json is None:
            logger.error(
                EXECUTION_RESUME_FAILED,
                agent_id=agent_id,
                task_id=task_id,
                error="checkpoint_context_json is None but can_resume was True",
            )
            msg = "checkpoint_context_json is None but can_resume was True"
            raise RuntimeError(msg)
        return recovery_result.checkpoint_context_json

    async def _resume_from_checkpoint(  # noqa: PLR0913
        self,
        recovery_result: RecoveryResult,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        *,
        completion_config: CompletionConfig | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
        provider: CompletionProvider | None = None,
        project_id: str | None = None,
    ) -> ExecutionResult:
        """Resume execution from a checkpoint.

        Returns:
            The :class:`ExecutionResult` from the resumed loop, after
            post-execution cost recording and task-state transitions
            have been applied.
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

        try:
            result, execution_id = await self._reconstruct_and_run_resume(
                checkpoint_json,
                recovery_result.error_message,
                agent_id,
                task_id,
                failure_category=recovery_result.failure_category,
                criteria_failed=recovery_result.criteria_failed,
                completion_config=completion_config,
                effective_autonomy=effective_autonomy,
                provider=provider,
                project_id=project_id,
                project_budget=project_budget,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                EXECUTION_RESUME_FAILED,
                agent_id=agent_id,
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        else:
            return await self._finalize_resume(
                result,
                identity,
                execution_id,
                agent_id,
                task_id,
                project_id=project_id,
            )

    async def _reconstruct_and_run_resume(  # noqa: PLR0913
        self,
        checkpoint_context_json: str,
        error_message: str,
        agent_id: str,
        task_id: str,
        *,
        failure_category: FailureCategory,
        criteria_failed: tuple[str, ...] = (),
        completion_config: CompletionConfig | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
        provider: CompletionProvider | None = None,
        project_id: str | None = None,
        project_budget: float = 0.0,
    ) -> tuple[ExecutionResult, str]:
        """Deserialize checkpoint context and run the resumed loop.

        Returns:
            ``(execution_result, execution_id)`` where
            ``execution_result`` is the loop's outcome and
            ``execution_id`` is the reconciled checkpoint's execution
            id (used by :py:meth:`_finalize_resume` for cleanup).
        """
        checkpoint_ctx = deserialize_and_reconcile(
            checkpoint_context_json,
            error_message,
            agent_id,
            task_id,
            failure_category=failure_category,
            criteria_failed=criteria_failed,
        )
        result = await self._execute_resumed_loop(
            checkpoint_ctx,
            agent_id,
            task_id,
            completion_config=completion_config,
            effective_autonomy=effective_autonomy,
            provider=provider,
            project_id=project_id,
            project_budget=project_budget,
        )
        return result, checkpoint_ctx.execution_id

    async def _execute_resumed_loop(  # noqa: PLR0913
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
            ),
            budget_checker=budget_checker,
            shutdown_checker=self._shutdown_checker,
            completion_config=completion_config,
        )
        return result

    async def _finalize_resume(  # noqa: PLR0913
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
        """
        await record_execution_costs(
            result,
            identity,
            agent_id,
            task_id,
            tracker=self._cost_tracker,
            project_id=project_id,
        )
        result = await apply_post_execution_transitions(
            result,
            agent_id,
            task_id,
            self._task_engine,
        )
        logger.info(
            EXECUTION_RESUME_COMPLETE,
            agent_id=agent_id,
            task_id=task_id,
            termination_reason=result.termination_reason.value,
        )
        if result.termination_reason != TerminationReason.ERROR:
            if self._recovery_strategy is not None:
                await self._recovery_strategy.finalize(execution_id)
            await cleanup_checkpoint_artifacts(
                self._checkpoint_repo,
                self._heartbeat_repo,
                execution_id,
            )
        return result
