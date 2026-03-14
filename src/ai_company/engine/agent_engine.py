"""Agent engine — top-level orchestrator.

Ties together prompt construction, execution context, execution loop,
tool invocation, and budget tracking into a single ``run()`` entry point.
"""

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

from ai_company.budget.errors import BudgetExhaustedError
from ai_company.engine._security_factory import (
    make_security_interceptor,
    registry_with_approval_tool,
)
from ai_company.engine._validation import (
    validate_agent,
    validate_run_inputs,
    validate_task,
)
from ai_company.engine.approval_gate import ApprovalGate
from ai_company.engine.checkpoint.models import CheckpointConfig
from ai_company.engine.checkpoint.resume import (
    cleanup_checkpoint_artifacts,
    deserialize_and_reconcile,
    make_loop_with_callback,
)
from ai_company.engine.classification.pipeline import classify_execution_errors
from ai_company.engine.context import DEFAULT_MAX_TURNS, AgentContext
from ai_company.engine.cost_recording import record_execution_costs
from ai_company.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
    make_budget_checker,
)
from ai_company.engine.metrics import TaskCompletionMetrics
from ai_company.engine.prompt import (
    SystemPrompt,
    build_error_prompt,
    build_system_prompt,
    format_task_instruction,
)
from ai_company.engine.react_loop import ReactLoop
from ai_company.engine.recovery import (
    FailAndReassignStrategy,
    RecoveryResult,
    RecoveryStrategy,
)
from ai_company.engine.run_result import AgentRunResult
from ai_company.engine.task_sync import (
    apply_post_execution_transitions,
    sync_to_task_engine,
    transition_task_if_needed,
)
from ai_company.observability import get_logger
from ai_company.observability.events.approval_gate import (
    APPROVAL_GATE_LOOP_WIRING_WARNING,
)
from ai_company.observability.events.execution import (
    EXECUTION_ENGINE_BUDGET_STOPPED,
    EXECUTION_ENGINE_COMPLETE,
    EXECUTION_ENGINE_CREATED,
    EXECUTION_ENGINE_ERROR,
    EXECUTION_ENGINE_PROMPT_BUILT,
    EXECUTION_ENGINE_START,
    EXECUTION_ENGINE_TASK_METRICS,
    EXECUTION_ENGINE_TASK_TRANSITION,
    EXECUTION_ENGINE_TIMEOUT,
    EXECUTION_RECOVERY_FAILED,
    EXECUTION_RESUME_COMPLETE,
    EXECUTION_RESUME_FAILED,
    EXECUTION_RESUME_START,
)
from ai_company.observability.events.prompt import PROMPT_TOKEN_RATIO_HIGH
from ai_company.providers.enums import MessageRole
from ai_company.providers.models import ChatMessage
from ai_company.security.audit import AuditLog
from ai_company.security.autonomy.models import EffectiveAutonomy  # noqa: TC001
from ai_company.tools.invoker import ToolInvoker
from ai_company.tools.permissions import ToolPermissionChecker

if TYPE_CHECKING:
    from ai_company.api.approval_store import ApprovalStore
    from ai_company.budget.coordination_config import ErrorTaxonomyConfig
    from ai_company.budget.enforcer import BudgetEnforcer
    from ai_company.budget.tracker import CostTracker
    from ai_company.core.agent import AgentIdentity
    from ai_company.core.task import Task
    from ai_company.engine.loop_protocol import (
        BudgetChecker,
        ExecutionLoop,
        ShutdownChecker,
    )
    from ai_company.engine.task_engine import TaskEngine
    from ai_company.persistence.repositories import (
        CheckpointRepository,
        HeartbeatRepository,
        ParkedContextRepository,
    )
    from ai_company.providers.models import CompletionConfig
    from ai_company.providers.protocol import CompletionProvider
    from ai_company.security.config import SecurityConfig
    from ai_company.security.protocol import SecurityInterceptionStrategy
    from ai_company.tools.registry import ToolRegistry

logger = get_logger(__name__)

_PROMPT_TOKEN_RATIO_THRESHOLD: float = 0.3
"""Prompt-to-total token ratio above which a warning is emitted."""

_DEFAULT_RECOVERY_STRATEGY = FailAndReassignStrategy()
"""Module-level default instance for the recovery strategy."""


class AgentEngine:
    """Top-level orchestrator for agent execution.

    Builds the system prompt, creates an execution context, delegates
    to the configured ``ExecutionLoop``, and returns an ``AgentRunResult``
    with full metadata.

    Args:
        provider: LLM completion provider (required).
        execution_loop: Defaults to ``ReactLoop()``.
        tool_registry: Optional tools available to the agent.
        cost_tracker: Falls back to ``budget_enforcer.cost_tracker``
            when ``None`` and ``budget_enforcer`` is provided. Must
            match ``budget_enforcer.cost_tracker`` if both supplied.
        recovery_strategy: Defaults to ``FailAndReassignStrategy``.
        shutdown_checker: Returns ``True`` for graceful shutdown.
        error_taxonomy_config: Post-execution error classification.
        budget_enforcer: Pre-flight checks, auto-downgrade, and
            enhanced in-flight budget checking.
        security_config: Optional security subsystem configuration.
        approval_store: Optional approval queue store.
        task_engine: Optional centralized task engine for real-time
            status sync (incremental transitions at each lifecycle
            point, best-effort).
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        provider: CompletionProvider,
        execution_loop: ExecutionLoop | None = None,
        tool_registry: ToolRegistry | None = None,
        cost_tracker: CostTracker | None = None,
        recovery_strategy: RecoveryStrategy | None = _DEFAULT_RECOVERY_STRATEGY,
        shutdown_checker: ShutdownChecker | None = None,
        error_taxonomy_config: ErrorTaxonomyConfig | None = None,
        budget_enforcer: BudgetEnforcer | None = None,
        security_config: SecurityConfig | None = None,
        approval_store: ApprovalStore | None = None,
        parked_context_repo: ParkedContextRepository | None = None,
        task_engine: TaskEngine | None = None,
        checkpoint_repo: CheckpointRepository | None = None,
        heartbeat_repo: HeartbeatRepository | None = None,
        checkpoint_config: CheckpointConfig | None = None,
    ) -> None:
        self._provider = provider
        self._approval_store = approval_store
        self._parked_context_repo = parked_context_repo
        self._approval_gate = self._make_approval_gate()
        if execution_loop is not None and self._approval_gate is not None:
            logger.warning(
                APPROVAL_GATE_LOOP_WIRING_WARNING,
                note=(
                    "execution_loop provided externally — approval_gate "
                    "will NOT be wired automatically. Configure the loop "
                    "with approval_gate= explicitly."
                ),
            )
        self._loop: ExecutionLoop = execution_loop or self._make_default_loop()
        self._tool_registry = tool_registry
        self._budget_enforcer = budget_enforcer
        if (checkpoint_repo is None) != (heartbeat_repo is None):
            msg = (
                "checkpoint_repo and heartbeat_repo must both be "
                "provided or both omitted"
            )
            raise ValueError(msg)
        self._checkpoint_repo = checkpoint_repo
        self._heartbeat_repo = heartbeat_repo
        self._checkpoint_config = checkpoint_config or CheckpointConfig()
        self._cost_tracker: CostTracker | None
        if budget_enforcer is not None:
            if (
                cost_tracker is not None
                and cost_tracker is not budget_enforcer.cost_tracker
            ):
                msg = (
                    "cost_tracker must match budget_enforcer.cost_tracker "
                    "when budget_enforcer is provided"
                )
                raise ValueError(msg)
            self._cost_tracker = budget_enforcer.cost_tracker
        else:
            self._cost_tracker = cost_tracker
        self._security_config = security_config
        self._task_engine = task_engine
        self._recovery_strategy = recovery_strategy
        self._shutdown_checker = shutdown_checker
        self._error_taxonomy_config = error_taxonomy_config
        self._audit_log = AuditLog()
        logger.debug(
            EXECUTION_ENGINE_CREATED,
            loop_type=self._loop.get_loop_type(),
            has_tool_registry=self._tool_registry is not None,
            has_cost_tracker=self._cost_tracker is not None,
            has_budget_enforcer=self._budget_enforcer is not None,
        )

    async def run(  # noqa: PLR0913
        self,
        *,
        identity: AgentIdentity,
        task: Task,
        completion_config: CompletionConfig | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        memory_messages: tuple[ChatMessage, ...] = (),
        timeout_seconds: float | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
    ) -> AgentRunResult:
        """Execute an agent on a task.

        Raises:
            ExecutionStateError: If pre-flight validation fails.
            ValueError: If ``max_turns < 1`` or ``timeout_seconds <= 0``.
            MemoryError: Re-raised unconditionally (non-recoverable).
            RecursionError: Re-raised unconditionally (non-recoverable).
        """
        agent_id = str(identity.id)
        task_id = task.id

        validate_run_inputs(
            agent_id=agent_id,
            task_id=task_id,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
        )
        validate_agent(identity, agent_id)
        validate_task(task, agent_id, task_id)

        logger.info(
            EXECUTION_ENGINE_START,
            agent_id=agent_id,
            task_id=task_id,
            loop_type=self._loop.get_loop_type(),
            max_turns=max_turns,
        )

        start = time.monotonic()
        ctx: AgentContext | None = None
        system_prompt: SystemPrompt | None = None
        try:
            # Pre-flight budget enforcement
            if self._budget_enforcer:
                await self._budget_enforcer.check_can_execute(agent_id)
                identity = await self._budget_enforcer.resolve_model(identity)

            tool_invoker = self._make_tool_invoker(
                identity,
                task_id=task_id,
                effective_autonomy=effective_autonomy,
            )
            ctx, system_prompt = await self._prepare_context(
                identity=identity,
                task=task,
                agent_id=agent_id,
                task_id=task_id,
                max_turns=max_turns,
                memory_messages=memory_messages,
                tool_invoker=tool_invoker,
                effective_autonomy=effective_autonomy,
            )
            return await self._execute(
                identity=identity,
                task=task,
                agent_id=agent_id,
                task_id=task_id,
                completion_config=completion_config,
                ctx=ctx,
                system_prompt=system_prompt,
                start=start,
                timeout_seconds=timeout_seconds,
                tool_invoker=tool_invoker,
                effective_autonomy=effective_autonomy,
            )
        except MemoryError, RecursionError:
            logger.exception(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error="non-recoverable error in run()",
            )
            raise
        except BudgetExhaustedError as exc:
            return self._handle_budget_error(
                exc=exc,
                identity=identity,
                task=task,
                agent_id=agent_id,
                task_id=task_id,
                duration_seconds=time.monotonic() - start,
                ctx=ctx,
                system_prompt=system_prompt,
            )
        except Exception as exc:
            return await self._handle_fatal_error(
                exc=exc,
                identity=identity,
                task=task,
                agent_id=agent_id,
                task_id=task_id,
                duration_seconds=time.monotonic() - start,
                ctx=ctx,
                system_prompt=system_prompt,
                completion_config=completion_config,
                effective_autonomy=effective_autonomy,
            )

    async def _execute(  # noqa: PLR0913
        self,
        *,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        completion_config: CompletionConfig | None,
        ctx: AgentContext,
        system_prompt: SystemPrompt,
        start: float,
        timeout_seconds: float | None = None,
        tool_invoker: ToolInvoker | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
    ) -> AgentRunResult:
        """Run execution loop, record costs, apply transitions, and build result."""
        budget_checker: BudgetChecker | None
        if self._budget_enforcer:
            budget_checker = await self._budget_enforcer.make_budget_checker(
                task,
                agent_id,
            )
        else:
            budget_checker = make_budget_checker(task)

        logger.debug(
            EXECUTION_ENGINE_PROMPT_BUILT,
            agent_id=agent_id,
            task_id=task_id,
            estimated_tokens=system_prompt.estimated_tokens,
        )

        execution_result = await self._run_loop_with_timeout(
            ctx=ctx,
            agent_id=agent_id,
            task_id=task_id,
            completion_config=completion_config,
            budget_checker=budget_checker,
            tool_invoker=tool_invoker,
            start=start,
            timeout_seconds=timeout_seconds,
        )

        execution_result = await self._post_execution_pipeline(
            execution_result,
            identity,
            agent_id,
            task_id,
            completion_config=completion_config,
            effective_autonomy=effective_autonomy,
        )

        return self._build_and_log_result(
            execution_result,
            system_prompt,
            start,
            agent_id,
            task_id,
        )

    async def _post_execution_pipeline(  # noqa: PLR0913
        self,
        execution_result: ExecutionResult,
        identity: AgentIdentity,
        agent_id: str,
        task_id: str,
        *,
        completion_config: CompletionConfig | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
    ) -> ExecutionResult:
        """Post-execution: costs, transitions, recovery, classify.

        Each transition is synced to TaskEngine incrementally
        (best-effort).  Classification and sync failures are logged,
        never fatal.
        """
        # Costs are recorded BEFORE recovery intentionally — the
        # pre-recovery execution's cost (including partial turns that
        # led to the error) should be tracked.  The resumed execution
        # records its own costs inside _finalize_resume.
        await record_execution_costs(
            execution_result,
            identity,
            agent_id,
            task_id,
            tracker=self._cost_tracker,
        )
        execution_result = await apply_post_execution_transitions(
            execution_result,
            agent_id,
            task_id,
            self._task_engine,
        )
        if execution_result.termination_reason == TerminationReason.ERROR:
            pre_recovery_ctx = execution_result.context
            pre_recovery_status = (
                pre_recovery_ctx.task_execution.status
                if pre_recovery_ctx.task_execution is not None
                else None
            )
            execution_result = await self._apply_recovery(
                execution_result,
                identity,
                agent_id,
                task_id,
                completion_config=completion_config,
                effective_autonomy=effective_autonomy,
            )
            # Sync post-recovery status to TaskEngine (typically FAILED,
            # depends on recovery strategy).
            ctx = execution_result.context
            if (
                ctx.task_execution is not None
                and pre_recovery_status is not None
                and ctx.task_execution.status != pre_recovery_status
            ):
                logger.info(
                    EXECUTION_ENGINE_TASK_TRANSITION,
                    agent_id=agent_id,
                    task_id=task_id,
                    from_status=pre_recovery_status.value,
                    to_status=ctx.task_execution.status.value,
                )
                await sync_to_task_engine(
                    self._task_engine,
                    target_status=ctx.task_execution.status,
                    task_id=task_id,
                    agent_id=agent_id,
                    reason=f"Post-recovery status: {ctx.task_execution.status.value}",
                )
        # Clean up checkpoints and heartbeat on non-ERROR exits.
        # The ERROR path is handled inside _finalize_resume (resume)
        # and _delegate_to_fallback (fallback).  Normal completions
        # (COMPLETED, MAX_TURNS, BUDGET_EXHAUSTED, SHUTDOWN, PARKED)
        # bypass recovery entirely, so cleanup runs here.
        if execution_result.termination_reason != TerminationReason.ERROR:
            exec_id = execution_result.context.execution_id
            if self._recovery_strategy is not None:
                await self._recovery_strategy.finalize(exec_id)
            await cleanup_checkpoint_artifacts(
                self._checkpoint_repo,
                self._heartbeat_repo,
                exec_id,
            )
        # Classification is non-critical — never destroys a result.
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
                    error=f"classification failed: {type(exc).__name__}: {exc}",
                    exc_info=True,
                )
        return execution_result

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
        agent_id: str,
        task_id: str,
    ) -> ExecutionLoop:
        """Return the execution loop with a checkpoint callback if configured."""
        return make_loop_with_callback(
            self._loop,
            self._checkpoint_repo,
            self._heartbeat_repo,
            self._checkpoint_config,
            agent_id,
            task_id,
        )

    async def _run_loop_with_timeout(  # noqa: PLR0913
        self,
        *,
        ctx: AgentContext,
        agent_id: str,
        task_id: str,
        completion_config: CompletionConfig | None,
        budget_checker: BudgetChecker | None,
        tool_invoker: ToolInvoker | None,
        start: float,
        timeout_seconds: float | None,
    ) -> ExecutionResult:
        """Execute the loop, using ``asyncio.wait`` for timeout control.

        Uses ``asyncio.wait`` instead of ``asyncio.wait_for`` so that
        ``TimeoutError`` raised inside the loop propagates normally
        and is not conflated with the engine's wall-clock deadline.
        """
        loop = self._make_loop_with_callback(agent_id, task_id)
        coro = loop.execute(
            context=ctx,
            provider=self._provider,
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

    # ── Setup ────────────────────────────────────────────────────

    async def _prepare_context(  # noqa: PLR0913
        self,
        *,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        max_turns: int,
        memory_messages: tuple[ChatMessage, ...],
        tool_invoker: ToolInvoker | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
    ) -> tuple[AgentContext, SystemPrompt]:
        """Build system prompt and prepare execution context."""
        tool_defs = tool_invoker.get_permitted_definitions() if tool_invoker else ()
        system_prompt = build_system_prompt(
            agent=identity,
            task=task,
            available_tools=tool_defs,
            effective_autonomy=effective_autonomy,
        )

        ctx = AgentContext.from_identity(
            identity,
            task=task,
            max_turns=max_turns,
        )
        ctx = ctx.with_message(
            ChatMessage(role=MessageRole.SYSTEM, content=system_prompt.content),
        )
        for msg in memory_messages:
            ctx = ctx.with_message(msg)
        ctx = ctx.with_message(
            ChatMessage(
                role=MessageRole.USER,
                content=format_task_instruction(task),
            ),
        )

        ctx = await transition_task_if_needed(
            ctx,
            agent_id,
            task_id,
            self._task_engine,
        )
        return ctx, system_prompt

    # ── Helpers ──────────────────────────────────────────────────

    async def _apply_recovery(  # noqa: PLR0913
        self,
        execution_result: ExecutionResult,
        identity: AgentIdentity,
        agent_id: str,
        task_id: str,
        *,
        completion_config: CompletionConfig | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
    ) -> ExecutionResult:
        """Invoke the configured recovery strategy on error outcomes.

        The default strategy transitions the task to FAILED; checkpoint
        recovery may resume from a persisted checkpoint.  If no strategy
        is set or no task execution exists, returns the result unchanged.
        Recovery failures are logged but never block the error result.
        """
        if self._recovery_strategy is None:
            return execution_result
        ctx = execution_result.context
        if ctx.task_execution is None:
            return execution_result

        error_msg = execution_result.error_message or "Unknown error"
        try:
            recovery_result = await self._recovery_strategy.recover(
                task_execution=ctx.task_execution,
                error_message=error_msg,
                context=ctx,
            )

            # Checkpoint resume path
            if recovery_result.can_resume:
                return await self._resume_from_checkpoint(
                    recovery_result,
                    identity,
                    agent_id,
                    task_id,
                    completion_config=completion_config,
                    effective_autonomy=effective_autonomy,
                )

            updated_ctx = ctx.model_copy(
                update={"task_execution": recovery_result.task_execution},
            )
            return execution_result.model_copy(
                update={"context": updated_ctx},
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.exception(
                EXECUTION_RECOVERY_FAILED,
                agent_id=agent_id,
                task_id=task_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            return execution_result

    def _validate_checkpoint_json(
        self,
        recovery_result: RecoveryResult,
        agent_id: str,
        task_id: str,
    ) -> str:
        """Return checkpoint JSON or raise if unexpectedly absent."""
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
        agent_id: str,
        task_id: str,
        *,
        completion_config: CompletionConfig | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
    ) -> ExecutionResult:
        """Resume execution from a checkpoint.

        Policy: resumed executions run without a wall-clock timeout.
        The loop's per-turn budget and max_turns still constrain
        execution.
        """
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
                completion_config=completion_config,
                effective_autonomy=effective_autonomy,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.exception(
                EXECUTION_RESUME_FAILED,
                agent_id=agent_id,
                task_id=task_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        else:
            return await self._finalize_resume(
                result,
                identity,
                execution_id,
                agent_id,
                task_id,
            )

    async def _reconstruct_and_run_resume(  # noqa: PLR0913
        self,
        checkpoint_context_json: str,
        error_message: str,
        agent_id: str,
        task_id: str,
        *,
        completion_config: CompletionConfig | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
    ) -> tuple[ExecutionResult, str]:
        """Deserialize checkpoint context and run the resumed loop.

        Returns:
            A ``(result, execution_id)`` tuple so the caller can
            call ``_finalize_resume`` with the execution identifier.
        """
        checkpoint_ctx = deserialize_and_reconcile(
            checkpoint_context_json,
            error_message,
            agent_id,
            task_id,
        )
        result = await self._execute_resumed_loop(
            checkpoint_ctx,
            agent_id,
            task_id,
            completion_config=completion_config,
            effective_autonomy=effective_autonomy,
        )
        return result, checkpoint_ctx.execution_id

    async def _execute_resumed_loop(
        self,
        checkpoint_ctx: AgentContext,
        agent_id: str,
        task_id: str,
        *,
        completion_config: CompletionConfig | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
    ) -> ExecutionResult:
        """Run the execution loop on a reconstituted checkpoint context."""
        budget_checker: BudgetChecker | None
        if checkpoint_ctx.task_execution is None:
            budget_checker = None
        elif self._budget_enforcer:
            budget_checker = await self._budget_enforcer.make_budget_checker(
                checkpoint_ctx.task_execution.task,
                agent_id,
            )
        else:
            budget_checker = make_budget_checker(
                checkpoint_ctx.task_execution.task,
            )

        loop = self._make_loop_with_callback(agent_id, task_id)
        return await loop.execute(
            context=checkpoint_ctx,
            provider=self._provider,
            tool_invoker=self._make_tool_invoker(
                checkpoint_ctx.identity,
                task_id=task_id,
                effective_autonomy=effective_autonomy,
            ),
            budget_checker=budget_checker,
            shutdown_checker=self._shutdown_checker,
            completion_config=completion_config,
        )

    async def _finalize_resume(
        self,
        result: ExecutionResult,
        identity: AgentIdentity,
        execution_id: str,
        agent_id: str,
        task_id: str,
    ) -> ExecutionResult:
        """Record costs, apply transitions, and clean up after resume.

        The resumed execution bypasses the normal pipeline's
        ``record_execution_costs`` and ``apply_post_execution_transitions``
        (those ran on the pre-crash result).  This method applies them
        to the resumed result so costs are tracked and task state is
        correctly transitioned.
        """
        await record_execution_costs(
            result,
            identity,
            agent_id,
            task_id,
            tracker=self._cost_tracker,
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

    def _make_approval_gate(self) -> ApprovalGate | None:
        """Build an ApprovalGate if an approval store is configured.

        Returns ``None`` when no approval store is available — the
        execution loop skips approval-gate checks in that case.
        """
        if self._approval_store is None:
            return None

        from ai_company.security.timeout.park_service import (  # noqa: PLC0415
            ParkService,
        )

        return ApprovalGate(
            park_service=ParkService(),
            parked_context_repo=self._parked_context_repo,
        )

    def _make_default_loop(self) -> ReactLoop:
        """Build the default ReactLoop with approval gate if available."""
        return ReactLoop(approval_gate=self._approval_gate)

    def _make_security_interceptor(
        self,
        effective_autonomy: EffectiveAutonomy | None = None,
    ) -> SecurityInterceptionStrategy | None:
        """Build the SecOps security interceptor if configured."""
        return make_security_interceptor(
            self._security_config,
            self._audit_log,
            approval_store=self._approval_store,
            effective_autonomy=effective_autonomy,
        )

    def _make_tool_invoker(
        self,
        identity: AgentIdentity,
        task_id: str | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
    ) -> ToolInvoker | None:
        """Create a ToolInvoker with permission checking and security."""
        if self._tool_registry is None:
            return None

        registry = registry_with_approval_tool(
            self._tool_registry,
            self._approval_store,
            identity,
            task_id=task_id,
        )
        checker = ToolPermissionChecker.from_permissions(identity.tools)
        interceptor = self._make_security_interceptor(effective_autonomy)
        return ToolInvoker(
            registry,
            permission_checker=checker,
            security_interceptor=interceptor,
            agent_id=str(identity.id),
            task_id=task_id,
        )

    def _log_completion(
        self,
        result: AgentRunResult,
        agent_id: str,
        task_id: str,
        duration: float,
    ) -> None:
        """Log structured completion event and proxy overhead metrics."""
        accumulated = result.execution_result.context.accumulated_cost
        logger.info(
            EXECUTION_ENGINE_COMPLETE,
            agent_id=agent_id,
            task_id=task_id,
            termination_reason=result.termination_reason.value,
            total_turns=result.total_turns,
            total_tokens=accumulated.total_tokens,
            duration_seconds=duration,
            cost_usd=result.total_cost_usd,
        )

        metrics = TaskCompletionMetrics.from_run_result(result)
        logger.info(
            EXECUTION_ENGINE_TASK_METRICS,
            agent_id=agent_id,
            task_id=task_id,
            termination_reason=result.termination_reason.value,
            turns_per_task=metrics.turns_per_task,
            tokens_per_task=metrics.tokens_per_task,
            cost_per_task=metrics.cost_per_task,
            duration_seconds=metrics.duration_seconds,
            prompt_tokens=metrics.prompt_tokens,
            prompt_token_ratio=metrics.prompt_token_ratio,
        )

        if metrics.prompt_token_ratio > _PROMPT_TOKEN_RATIO_THRESHOLD:
            logger.warning(
                PROMPT_TOKEN_RATIO_HIGH,
                agent_id=agent_id,
                task_id=task_id,
                prompt_token_ratio=metrics.prompt_token_ratio,
                prompt_tokens=metrics.prompt_tokens,
                total_tokens=metrics.tokens_per_task,
            )

    def _handle_budget_error(  # noqa: PLR0913
        self,
        *,
        exc: BudgetExhaustedError,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        duration_seconds: float,
        ctx: AgentContext | None = None,
        system_prompt: SystemPrompt | None = None,
    ) -> AgentRunResult:
        """Build a BUDGET_EXHAUSTED result (no recovery — controlled stop)."""
        logger.warning(
            EXECUTION_ENGINE_BUDGET_STOPPED,
            agent_id=agent_id,
            task_id=task_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        try:
            error_ctx = ctx or AgentContext.from_identity(identity, task=task)
            budget_result = ExecutionResult(
                context=error_ctx,
                termination_reason=TerminationReason.BUDGET_EXHAUSTED,
            )
            error_prompt = build_error_prompt(
                identity,
                agent_id,
                system_prompt,
            )
            return AgentRunResult(
                execution_result=budget_result,
                system_prompt=error_prompt,
                duration_seconds=duration_seconds,
                agent_id=agent_id,
                task_id=task_id,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as build_exc:
            logger.exception(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error=f"Failed to build budget-exhausted result: {build_exc}",
            )
            raise exc from None

    async def _handle_fatal_error(  # noqa: PLR0913
        self,
        *,
        exc: Exception,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        duration_seconds: float,
        ctx: AgentContext | None = None,
        system_prompt: SystemPrompt | None = None,
        completion_config: CompletionConfig | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
    ) -> AgentRunResult:
        """Build an error ``AgentRunResult`` when the execution pipeline fails.

        If constructing the error result itself fails, the original
        exception is re-raised so it is never silently lost.
        """
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.exception(
            EXECUTION_ENGINE_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            error=error_msg,
        )

        pre_fatal_status = (
            ctx.task_execution.status
            if ctx is not None and ctx.task_execution is not None
            else None
        )
        try:
            error_execution = await self._build_error_execution(
                identity,
                task,
                agent_id,
                task_id,
                error_msg,
                ctx,
                completion_config=completion_config,
                effective_autonomy=effective_autonomy,
            )
            # Sync fatal-error recovery status to TaskEngine (best-effort).
            error_ctx = error_execution.context
            if (
                error_ctx.task_execution is not None
                and pre_fatal_status is not None
                and error_ctx.task_execution.status != pre_fatal_status
            ):
                logger.info(
                    EXECUTION_ENGINE_TASK_TRANSITION,
                    agent_id=agent_id,
                    task_id=task_id,
                    from_status=pre_fatal_status.value,
                    to_status=error_ctx.task_execution.status.value,
                )
                await sync_to_task_engine(
                    self._task_engine,
                    target_status=error_ctx.task_execution.status,
                    task_id=task_id,
                    agent_id=agent_id,
                    reason=f"Fatal error recovery: {type(exc).__name__}",
                )
            error_prompt = build_error_prompt(
                identity,
                agent_id,
                system_prompt,
            )
            return AgentRunResult(
                execution_result=error_execution,
                system_prompt=error_prompt,
                duration_seconds=duration_seconds,
                agent_id=agent_id,
                task_id=task_id,
            )
        except MemoryError, RecursionError:
            logger.exception(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error="non-recoverable error while building error result",
            )
            raise
        except Exception as build_exc:
            logger.exception(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error=f"Failed to build error result: {build_exc}",
                original_error=error_msg,
            )
            raise exc from None

    async def _build_error_execution(  # noqa: PLR0913
        self,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        error_msg: str,
        ctx: AgentContext | None,
        *,
        completion_config: CompletionConfig | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
    ) -> ExecutionResult:
        """Create an error ``ExecutionResult`` and apply recovery."""
        error_ctx = ctx or AgentContext.from_identity(identity, task=task)
        error_execution = ExecutionResult(
            context=error_ctx,
            termination_reason=TerminationReason.ERROR,
            error_message=error_msg,
        )
        return await self._apply_recovery(
            error_execution,
            identity,
            agent_id,
            task_id,
            completion_config=completion_config,
            effective_autonomy=effective_autonomy,
        )
