# module-kind: service
"""Post-execution pipeline mixin for :class:`AgentEngine`."""

import asyncio
from typing import TYPE_CHECKING, Final

from synthorg.budget.coordination_collector import CollectionInputs
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.checkpoint.resume import (
    cleanup_checkpoint_artifacts,
    make_loop_with_callback,
)
from synthorg.engine.classification.pipeline import classify_execution_errors
from synthorg.engine.context import AgentContext
from synthorg.engine.cost_recording import (
    record_execution_costs,
    resolve_tracker_currency,
)
from synthorg.engine.loop_protocol import (
    BudgetChecker,
    ExecutionLoop,
    ExecutionResult,
    ShutdownChecker,
    TaskCancellationChecker,
    TerminationReason,
)
from synthorg.engine.prompt import SystemPrompt
from synthorg.engine.recovery import RecoveryResult, RecoveryStrategy
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
from synthorg.providers.models import CompletionConfig
from synthorg.providers.protocol import CompletionProvider
from synthorg.tools.protocol import ToolInvokerProtocol

if TYPE_CHECKING:
    from collections.abc import Callable

    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.budget.coordination_collector import CoordinationMetricsCollector
    from synthorg.budget.coordination_config import ErrorTaxonomyConfig
    from synthorg.budget.tracker import CostTracker
    from synthorg.core.clock import Clock
    from synthorg.core.effective_autonomy import EffectiveAutonomy
    from synthorg.engine._agent_engine_callables import ApplyRecovery
    from synthorg.engine.checkpoint.models import CheckpointConfig
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.memory.procedural.capture.protocol import CaptureStrategy
    from synthorg.memory.procedural.models import ProceduralMemoryConfig
    from synthorg.memory.procedural.proposer import ProceduralMemoryProposer
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.persistence.checkpoint_protocol import (
        CheckpointRepository,
        HeartbeatRepository,
    )

logger = get_logger(__name__)

_TRANSITION_REASON_CRITERIA_CAP: Final[int] = 5

#: Grace window granted to a cancelled inner loop task so its finally-block
#: cleanup (checkpoint / teardown writes) can settle before the post-execution
#: pipeline writes the terminal status. Bounded so a loop wedged in synchronous
#: work or swallowing cancellation cannot keep the worker hung past the
#: wall-clock timeout.
_CANCEL_GRACE_SECONDS: Final[float] = 2.0


class AgentEnginePostExecMixin:
    """Mixin providing post-execution, timeout wrapper, and result builder."""

    _cost_tracker: CostTracker | None
    _task_engine: TaskEngine | None
    _approval_store: ApprovalStoreProtocol | None
    _apply_recovery: ApplyRecovery
    _recovery_strategy: RecoveryStrategy | None
    _checkpoint_repo: CheckpointRepository | None
    _heartbeat_repo: HeartbeatRepository | None
    _error_taxonomy_config: ErrorTaxonomyConfig | None
    _checkpoint_config: CheckpointConfig
    _coordination_metrics_collector: CoordinationMetricsCollector | None
    _distillation_capture_enabled: bool
    _log_completion: Callable[[AgentRunResult, str, str, float], None]
    _memory_backend: MemoryBackend | None
    _procedural_memory_config: ProceduralMemoryConfig | None
    _procedural_proposer: ProceduralMemoryProposer | None
    _capture_strategy: CaptureStrategy | None
    _provider: CompletionProvider
    _shutdown_checker: ShutdownChecker | None
    # Injected by ``AgentEngine.__init__``; declared on the mixin so
    # type checkers see the attribute when the helper accesses it
    # below. The concrete class owns the assignment.
    _clock: Clock

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
        """Post-execution: costs, transitions, recovery, classify.

        Returns:
            The :class:`ExecutionResult` after cost recording, status
            transitions, optional recovery, checkpoint cleanup, and
            best-effort classification / distillation / coordination
            metrics hooks have all run.
        """
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
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    EXECUTION_ENGINE_ERROR,
                    agent_id=agent_id,
                    task_id=task_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    reason="classification_failed",
                )
        await self._try_procedural_memory(
            failed_result or execution_result,
            recovery_result,
            agent_id,
            task_id,
        )
        await self._try_capture_success(
            execution_result,
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

        Returns:
            ``(execution_result, recovery_result, failed_result)``:
            the post-recovery execution, the optional
            :class:`RecoveryResult` diagnosis, and the original failed
            execution (preserved for procedural-memory capture).
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
        from_status: TaskStatus,
        to_status: TaskStatus,
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
                CollectionInputs(
                    execution_result=execution_result,
                    agent_id=agent_id,
                    task_id=task_id,
                    is_multi_agent=False,
                ),
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="coordination_metrics_failed",
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

    async def _try_capture_success(
        self,
        execution_result: ExecutionResult,
        recovery_result: RecoveryResult | None,
        agent_id: str,
        task_id: str,
    ) -> None:
        """Run the success-capture strategy post-execution (non-critical)."""
        from synthorg.engine.post_execution import (  # noqa: PLC0415
            try_capture_success,
        )

        await try_capture_success(
            execution_result,
            recovery_result,
            agent_id,
            task_id,
            capture_strategy=self._capture_strategy,
            memory_backend=self._memory_backend,
        )

    def _build_and_log_result(
        self,
        execution_result: ExecutionResult,
        system_prompt: SystemPrompt,
        start: float,
        agent_id: str,
        task_id: str,
    ) -> AgentRunResult:
        """Build ``AgentRunResult`` and log completion metrics.

        Returns:
            The :class:`AgentRunResult` carrying the execution result,
            system prompt, duration, and resolved cost-tracker currency.
        """
        duration = self._clock.monotonic() - start
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                note="Completion logging failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        return result

    def _make_loop_with_callback(
        self,
        loop: ExecutionLoop,
        agent_id: str,
        task_id: str,
    ) -> ExecutionLoop:
        """Return the execution loop with a checkpoint callback if configured.

        Returns:
            The original loop wrapped with a checkpoint-write callback
            when ``_checkpoint_config`` and the repositories are
            wired; otherwise the loop is returned unchanged.
        """
        return make_loop_with_callback(
            loop,
            self._checkpoint_repo,
            self._heartbeat_repo,
            self._checkpoint_config,
            agent_id,
            task_id,
        )

    def _make_task_cancellation_checker(
        self,
        task_id: str,
    ) -> TaskCancellationChecker | None:
        """Build a per-task cancellation checker if a TaskEngine is wired.

        The closure reads the task's authoritative status at each safe boundary
        and reports cancellation, the durable cross-process supersession signal
        (the operator cancels in the API process; the agent runs in the worker).

        Returns:
            An async ``() -> bool`` checker, or ``None`` when no TaskEngine is
            wired (cancellation observation is then disabled).
        """
        task_engine = self._task_engine
        if task_engine is None:
            return None

        async def _check() -> bool:
            task = await task_engine.get_task(task_id)
            return task is not None and task.status == TaskStatus.CANCELLED

        return _check

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
        """Execute the loop, using ``asyncio.wait`` for timeout control.

        Returns:
            The loop's :class:`ExecutionResult` on successful completion
            within ``timeout_seconds``; an ERROR-terminated
            ``ExecutionResult`` carrying a wall-clock timeout message
            when the timeout fires (the inner task is cancelled).
        """
        wrapped_loop = self._make_loop_with_callback(loop, agent_id, task_id)
        coro = wrapped_loop.execute(
            context=ctx,
            provider=provider or self._provider,
            tool_invoker=tool_invoker,
            budget_checker=budget_checker,
            shutdown_checker=self._shutdown_checker,
            completion_config=completion_config,
            task_cancellation_checker=self._make_task_cancellation_checker(task_id),
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

        duration = self._clock.monotonic() - start
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
        # Bounded grace window: let the cancelled task's finally-block
        # cleanup (checkpoint / teardown writes) settle before the
        # post-execution pipeline writes the terminal status, so a late
        # checkpoint cannot land after the CANCELLED/ERROR transition.
        # ``asyncio.wait`` never cancels on timeout, so a loop wedged in
        # synchronous work or swallowing cancellation simply exhausts the
        # grace window; we then fall through to a detached done-callback,
        # preserving the wall-clock timeout contract.
        _settled, still_pending = await asyncio.wait(
            {loop_task}, timeout=_CANCEL_GRACE_SECONDS
        )
        if still_pending:
            # Loop did not honour cancellation within the grace window; detach
            # and let a done-callback retrieve the eventual result so a later
            # failure is not logged as an unretrieved task exception.
            loop_task.add_done_callback(lambda t: t.cancelled() or t.exception())
        elif not loop_task.cancelled():
            # Settled with a non-cancellation failure: retrieve it so it is not
            # surfaced as an unretrieved task exception. (A cancelled task's
            # ``exception()`` would itself raise, hence the guard.)
            _ = loop_task.exception()
        return ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.ERROR,
            error_message=error_msg,
        )
