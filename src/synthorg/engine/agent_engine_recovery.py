"""Recovery mixin for :class:`AgentEngine`.

Owns the decision half of recovery: run the configured strategy on an error
outcome, and either resume from its checkpoint or carry its updated task
execution forward. The resume mechanics live in
:class:`AgentEngineCheckpointResumeMixin`, which this inherits, so the engine
still sees one flat surface.
"""

from typing import TYPE_CHECKING

from synthorg.budget.errors import BudgetExhaustedError
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.engine.agent_engine_checkpoint_resume import (
    AgentEngineCheckpointResumeMixin,
)
from synthorg.engine.checkpoint.resume import deserialize_and_reconcile
from synthorg.engine.errors import ProjectNotFoundError
from synthorg.engine.failure_classification import FailureCategory
from synthorg.engine.loop_protocol import ExecutionResult
from synthorg.engine.recovery import RecoveryResult
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_RECOVERY_FAILED,
    EXECUTION_RESUME_FAILED,
)
from synthorg.providers.models import CompletionConfig
from synthorg.providers.protocol import CompletionProvider

if TYPE_CHECKING:
    from synthorg.core.effective_autonomy import EffectiveAutonomy

logger = get_logger(__name__)


class AgentEngineRecoveryMixin(AgentEngineCheckpointResumeMixin):
    """Mixin providing recovery and checkpoint-resume helpers."""

    async def _apply_recovery(
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
            when recovery was declared absent, when the run carried no
            task execution to recover, or when recovery raised a
            non-typed error that was logged and swallowed).

        Raises:
            ProjectNotFoundError: Re-raised from the strategy when the
                project context is gone.
            BudgetExhaustedError: Re-raised from the strategy when
                resume cost would exceed the remaining budget.
        """
        if (
            self._recovery_strategy is None
            or execution_result.context.task_execution is None
        ):
            return execution_result, None
        try:
            return await self._run_recovery(
                execution_result,
                identity,
                agent_id,
                task_id,
                completion_config=completion_config,
                effective_autonomy=effective_autonomy,
                provider=provider,
                project_id=project_id,
            )
        except ProjectNotFoundError:
            raise
        except BudgetExhaustedError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort side channel
            reraise_critical(exc)
            logger.warning(
                EXECUTION_RECOVERY_FAILED,
                agent_id=agent_id,
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return execution_result, None

    async def _run_recovery(
        self,
        execution_result: ExecutionResult,
        identity: AgentIdentity,
        agent_id: str,
        task_id: str,
        *,
        completion_config: CompletionConfig | None,
        effective_autonomy: EffectiveAutonomy | None,
        provider: CompletionProvider | None,
        project_id: str | None,
    ) -> tuple[ExecutionResult, RecoveryResult | None]:
        """Run the strategy and apply whichever outcome it chose.

        Split from :meth:`_apply_recovery` so the typed re-raises and the
        best-effort swallow stay one flat handler chain around a single
        call, rather than wrapping the recovery logic they must not catch
        the wrong half of.

        Returns:
            ``(execution_result, recovery_result)``: the resumed execution
            when the strategy could resume, else the original execution
            carrying the strategy's updated task execution.
        """
        strategy = self._recovery_strategy
        ctx = execution_result.context
        execution = ctx.task_execution
        # The caller narrowed both; repeated here because neither narrowing
        # survives the call boundary.
        if strategy is None or execution is None:
            return execution_result, None
        recovery_result = await strategy.recover(
            task_execution=execution,
            error_message=execution_result.error_message or "Unknown error",
            context=ctx,
            error_type=execution_result.error_type,
        )
        if recovery_result.can_resume:
            resumed = await self._resume_from_checkpoint(
                recovery_result,
                identity,
                execution.task,
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
        updated = execution_result.model_copy(update={"context": updated_ctx})
        return updated, recovery_result

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
        prepared = await self._prepare_resume(
            recovery_result,
            task,
            agent_id,
            task_id,
            project_id=project_id,
        )
        try:
            result, execution_id = await self._reconstruct_and_run_resume(
                prepared.checkpoint_json,
                recovery_result.error_message,
                agent_id,
                task_id,
                failure_category=recovery_result.failure_category,
                criteria_failed=recovery_result.criteria_failed,
                completion_config=completion_config,
                effective_autonomy=effective_autonomy,
                provider=provider,
                project_id=prepared.project_id,
                project_budget=prepared.project_budget,
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
                project_id=prepared.project_id,
            )
