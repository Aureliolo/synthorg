"""Post-execution pipeline mixin for :class:`AgentEngine`."""

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any

from synthorg.engine.checkpoint.resume import (
    cleanup_checkpoint_artifacts,
    make_loop_with_callback,
)
from synthorg.engine.classification.pipeline import classify_execution_errors
from synthorg.engine.cost_recording import (
    record_execution_costs,
    resolve_tracker_currency,
)
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.recovery import RecoveryResult  # noqa: TC001
from synthorg.engine.run_result import AgentRunResult
from synthorg.engine.sanitization import sanitize_message
from synthorg.engine.task_sync import (
    apply_post_execution_transitions,
    sync_to_task_engine,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_ERROR,
    EXECUTION_ENGINE_TASK_TRANSITION,
    EXECUTION_ENGINE_TIMEOUT,
    EXECUTION_RECOVERY_DIAGNOSIS,
)

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.engine.context import AgentContext
    from synthorg.engine.loop_protocol import (
        BudgetChecker,
        ExecutionLoop,
    )
    from synthorg.engine.prompt import SystemPrompt
    from synthorg.providers.models import CompletionConfig
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.security.autonomy.models import EffectiveAutonomy
    from synthorg.tools.protocol import ToolInvokerProtocol

logger = get_logger(__name__)

_TRANSITION_REASON_CRITERIA_CAP = 5


class AgentEnginePostExecMixin:
    """Mixin providing post-execution, timeout wrapper, and result builder."""

    _cost_tracker: Any
    _task_engine: Any
    _approval_store: Any
    _apply_recovery: Any
    _recovery_strategy: Any
    _checkpoint_repo: Any
    _heartbeat_repo: Any
    _error_taxonomy_config: Any
    _checkpoint_config: Any
    _coordination_metrics_collector: Any
    _distillation_capture_enabled: Any
    _log_completion: Any
    _memory_backend: Any
    _procedural_memory_config: Any
    _procedural_proposer: Any
    _provider: Any
    _shutdown_checker: Any

    async def _post_execution_pipeline(  # noqa: PLR0913
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
    ) -> ExecutionResult:
        """Post-execution: costs, transitions, recovery, classify."""
        await record_execution_costs(
            execution_result,
            identity,
            agent_id,
            task_id,
            tracker=self._cost_tracker,
            project_id=project_id,
        )
        execution_result = await apply_post_execution_transitions(
            execution_result,
            agent_id,
            task_id,
            self._task_engine,
            approval_store=self._approval_store,
        )
        recovery_result: RecoveryResult | None = None
        failed_result: ExecutionResult | None = None
        if execution_result.termination_reason == TerminationReason.ERROR:
            (
                execution_result,
                recovery_result,
                failed_result,
            ) = await self._handle_error_recovery(
                execution_result,
                identity,
                agent_id,
                task_id,
                completion_config=completion_config,
                effective_autonomy=effective_autonomy,
                provider=provider,
                project_id=project_id,
            )
        if execution_result.termination_reason != TerminationReason.ERROR:
            exec_id = execution_result.context.execution_id
            if self._recovery_strategy is not None:
                await self._recovery_strategy.finalize(exec_id)
            await cleanup_checkpoint_artifacts(
                self._checkpoint_repo,
                self._heartbeat_repo,
                exec_id,
            )
        if self._error_taxonomy_config is not None:
            try:
                await classify_execution_errors(
                    execution_result,
                    agent_id,
                    task_id,
                    config=self._error_taxonomy_config,
                )
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    EXECUTION_ENGINE_ERROR,
                    agent_id=agent_id,
                    task_id=task_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    reason="classification_failed",
                    exc_info=True,
                )
        await self._try_procedural_memory(
            failed_result or execution_result,
            recovery_result,
            agent_id,
            task_id,
        )
        await self._try_capture_distillation(
            execution_result,
            agent_id,
            task_id,
        )
        await self._try_collect_coordination_metrics(
            execution_result,
            agent_id,
            task_id,
        )
        return execution_result

    async def _handle_error_recovery(  # noqa: PLR0913
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
    ) -> tuple[ExecutionResult, RecoveryResult | None, ExecutionResult]:
        """Run recovery for an ERROR termination.

        Returns the post-recovery ``execution_result``, the optional
        ``recovery_result`` diagnosis, and the original failed result
        (for downstream hooks like procedural-memory capture).
        """
        failed_result = execution_result
        pre_recovery_ctx = execution_result.context
        pre_recovery_status = (
            pre_recovery_ctx.task_execution.status
            if pre_recovery_ctx.task_execution is not None
            else None
        )
        execution_result, recovery_result = await self._apply_recovery(
            execution_result,
            identity,
            agent_id,
            task_id,
            completion_config=completion_config,
            effective_autonomy=effective_autonomy,
            provider=provider,
            project_id=project_id,
        )
        if recovery_result is not None:
            logger.info(
                EXECUTION_RECOVERY_DIAGNOSIS,
                agent_id=agent_id,
                task_id=task_id,
                failure_category=recovery_result.failure_category.value,
                criteria_failed_count=len(recovery_result.criteria_failed),
            )
        ctx = execution_result.context
        if (
            recovery_result is not None
            and ctx.task_execution is not None
            and pre_recovery_status is not None
            and ctx.task_execution.status != pre_recovery_status
        ):
            await self._log_post_recovery_transition(
                recovery_result,
                agent_id=agent_id,
                task_id=task_id,
                from_status=pre_recovery_status,
                to_status=ctx.task_execution.status,
            )
        return execution_result, recovery_result, failed_result

    async def _log_post_recovery_transition(
        self,
        recovery_result: RecoveryResult,
        *,
        agent_id: str,
        task_id: str,
        from_status: Any,
        to_status: Any,
    ) -> None:
        """Log the post-recovery task-status transition + sync to task engine."""
        logger.info(
            EXECUTION_ENGINE_TASK_TRANSITION,
            agent_id=agent_id,
            task_id=task_id,
            from_status=from_status.value,
            to_status=to_status.value,
        )
        category = recovery_result.failure_category.value
        criteria_suffix = ""
        if recovery_result.criteria_failed:
            capped = recovery_result.criteria_failed[:_TRANSITION_REASON_CRITERIA_CAP]
            sanitized = "; ".join(sanitize_message(c) for c in capped)
            overflow = (
                len(recovery_result.criteria_failed) - _TRANSITION_REASON_CRITERIA_CAP
            )
            more = f" +{overflow} more" if overflow > 0 else ""
            criteria_suffix = f", unmet_criteria={sanitized}{more}"
        await sync_to_task_engine(
            self._task_engine,
            target_status=to_status,
            task_id=task_id,
            agent_id=agent_id,
            reason=(
                f"Post-recovery status: {to_status.value} "
                f"(failure_category={category}{criteria_suffix})"
            ),
        )

    async def _try_capture_distillation(
        self,
        execution_result: ExecutionResult,
        agent_id: str,
        task_id: str,
    ) -> None:
        """Capture trajectory distillation at task completion (non-critical)."""
        from synthorg.engine.post_execution import (  # noqa: PLC0415
            try_capture_distillation,
        )

        await try_capture_distillation(
            execution_result,
            agent_id,
            task_id,
            distillation_capture_enabled=self._distillation_capture_enabled,
            memory_backend=self._memory_backend,
        )

    async def _try_collect_coordination_metrics(
        self,
        execution_result: ExecutionResult,
        agent_id: str,
        task_id: str,
    ) -> None:
        """Collect coordination metrics post-execution (non-critical, never fatal)."""
        if self._coordination_metrics_collector is None:
            return
        try:
            await self._coordination_metrics_collector.collect(
                execution_result=execution_result,
                agent_id=agent_id,
                task_id=task_id,
                is_multi_agent=False,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="coordination_metrics_failed",
                exc_info=True,
            )

    async def _try_procedural_memory(
        self,
        execution_result: ExecutionResult,
        recovery_result: RecoveryResult | None,
        agent_id: str,
        task_id: str,
    ) -> None:
        """Run procedural memory pipeline (non-critical, never fatal)."""
        from synthorg.engine.post_execution import (  # noqa: PLC0415
            try_procedural_memory,
        )

        await try_procedural_memory(
            execution_result,
            recovery_result,
            agent_id,
            task_id,
            procedural_proposer=self._procedural_proposer,
            memory_backend=self._memory_backend,
            procedural_memory_config=self._procedural_memory_config,
        )

    def _build_and_log_result(
        self,
        execution_result: ExecutionResult,
        system_prompt: SystemPrompt,
        start: float,
        agent_id: str,
        task_id: str,
    ) -> AgentRunResult:
        """Build ``AgentRunResult`` and log completion metrics."""
        duration = time.monotonic() - start
        result = AgentRunResult(
            execution_result=execution_result,
            system_prompt=system_prompt,
            duration_seconds=duration,
            agent_id=agent_id,
            task_id=task_id,
            currency=resolve_tracker_currency(self._cost_tracker),
        )
        try:
            self._log_completion(result, agent_id, task_id, duration)
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error="Completion logging failed",
            )
        return result

    def _make_loop_with_callback(
        self,
        loop: ExecutionLoop,
        agent_id: str,
        task_id: str,
    ) -> ExecutionLoop:
        """Return the execution loop with a checkpoint callback if configured."""
        return make_loop_with_callback(
            loop,
            self._checkpoint_repo,
            self._heartbeat_repo,
            self._checkpoint_config,
            agent_id,
            task_id,
        )

    async def _run_loop_with_timeout(  # noqa: PLR0913
        self,
        *,
        loop: ExecutionLoop,
        ctx: AgentContext,
        agent_id: str,
        task_id: str,
        completion_config: CompletionConfig | None,
        budget_checker: BudgetChecker | None,
        tool_invoker: ToolInvokerProtocol | None,
        start: float,
        timeout_seconds: float | None,
        provider: CompletionProvider | None = None,
    ) -> ExecutionResult:
        """Execute the loop, using ``asyncio.wait`` for timeout control."""
        wrapped_loop = self._make_loop_with_callback(loop, agent_id, task_id)
        coro = wrapped_loop.execute(
            context=ctx,
            provider=provider or self._provider,
            tool_invoker=tool_invoker,
            budget_checker=budget_checker,
            shutdown_checker=self._shutdown_checker,
            completion_config=completion_config,
        )
        if timeout_seconds is None:
            return await coro

        loop_task = asyncio.create_task(coro)
        _done, pending = await asyncio.wait(
            {loop_task},
            timeout=timeout_seconds,
        )
        if not pending:
            return loop_task.result()

        duration = time.monotonic() - start
        error_msg = (
            f"Wall-clock timeout after {duration:.1f}s (limit: {timeout_seconds}s)"
        )
        logger.warning(
            EXECUTION_ENGINE_TIMEOUT,
            agent_id=agent_id,
            task_id=task_id,
            duration_seconds=duration,
            timeout_seconds=timeout_seconds,
        )
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop_task
        return ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.ERROR,
            error_message=error_msg,
        )
