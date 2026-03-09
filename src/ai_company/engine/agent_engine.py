"""Agent engine — top-level orchestrator.

Ties together prompt construction, execution context, execution loop,
tool invocation, and budget tracking into a single ``run()`` entry point.
"""

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

from ai_company.core.enums import TaskStatus
from ai_company.engine.classification.pipeline import classify_execution_errors
from ai_company.engine.context import DEFAULT_MAX_TURNS, AgentContext
from ai_company.engine.cost_recording import record_execution_costs
from ai_company.engine.errors import ExecutionStateError
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
from ai_company.engine.recovery import FailAndReassignStrategy, RecoveryStrategy
from ai_company.engine.run_result import AgentRunResult
from ai_company.engine.validation import (
    validate_agent,
    validate_run_inputs,
    validate_task,
)
from ai_company.observability import get_logger
from ai_company.observability.events.execution import (
    EXECUTION_ENGINE_COMPLETE,
    EXECUTION_ENGINE_CREATED,
    EXECUTION_ENGINE_ERROR,
    EXECUTION_ENGINE_PROMPT_BUILT,
    EXECUTION_ENGINE_START,
    EXECUTION_ENGINE_TASK_METRICS,
    EXECUTION_ENGINE_TASK_TRANSITION,
    EXECUTION_ENGINE_TIMEOUT,
    EXECUTION_RECOVERY_FAILED,
)
from ai_company.providers.enums import MessageRole
from ai_company.providers.models import ChatMessage
from ai_company.tools.invoker import ToolInvoker
from ai_company.tools.permissions import ToolPermissionChecker

if TYPE_CHECKING:
    from ai_company.budget.coordination_config import ErrorTaxonomyConfig
    from ai_company.budget.tracker import CostTracker
    from ai_company.core.agent import AgentIdentity
    from ai_company.core.task import Task
    from ai_company.engine.loop_protocol import (
        BudgetChecker,
        ExecutionLoop,
        ShutdownChecker,
    )
    from ai_company.providers.models import CompletionConfig, ToolDefinition
    from ai_company.providers.protocol import CompletionProvider
    from ai_company.tools.registry import ToolRegistry

logger = get_logger(__name__)

_DEFAULT_RECOVERY_STRATEGY = FailAndReassignStrategy()
"""Module-level default instance for the recovery strategy."""


class AgentEngine:
    """Top-level orchestrator for agent execution.

    Builds the system prompt, creates an execution context, delegates
    to the configured ``ExecutionLoop``, and returns an ``AgentRunResult``
    with full metadata.

    Args:
        provider: LLM completion provider (required).
        execution_loop: Loop implementation. Defaults to ``ReactLoop()``.
        tool_registry: Optional tools available to the agent.
        cost_tracker: Optional cost recording service. When ``None``,
            cost recording is skipped silently.
        recovery_strategy: Crash recovery strategy. Defaults to a
            shared ``FailAndReassignStrategy`` instance. Pass ``None``
            to disable.
        shutdown_checker: Optional callback; returns ``True`` when a
            graceful shutdown has been requested.  Passed through to
            the execution loop.
        error_taxonomy_config: Optional error taxonomy configuration.
            When provided and enabled, runs post-execution
            classification of coordination errors.
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
    ) -> None:
        self._provider = provider
        self._loop: ExecutionLoop = execution_loop or ReactLoop()
        self._tool_registry = tool_registry
        self._cost_tracker = cost_tracker
        self._recovery_strategy = recovery_strategy
        self._shutdown_checker = shutdown_checker
        self._error_taxonomy_config = error_taxonomy_config
        logger.debug(
            EXECUTION_ENGINE_CREATED,
            loop_type=self._loop.get_loop_type(),
            has_tool_registry=self._tool_registry is not None,
            has_cost_tracker=self._cost_tracker is not None,
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
            tool_invoker = self._make_tool_invoker(identity)
            ctx, system_prompt = self._prepare_context(
                identity=identity,
                task=task,
                agent_id=agent_id,
                task_id=task_id,
                max_turns=max_turns,
                memory_messages=memory_messages,
                tool_invoker=tool_invoker,
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
            )
        except MemoryError, RecursionError:
            logger.error(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error="non-recoverable error in run()",
                exc_info=True,
            )
            raise
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
    ) -> AgentRunResult:
        """Run execution loop, record costs, apply transitions, and build result."""
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
        )

        return self._build_and_log_result(
            execution_result,
            system_prompt,
            start,
            agent_id,
            task_id,
        )

    async def _post_execution_pipeline(
        self,
        execution_result: ExecutionResult,
        identity: AgentIdentity,
        agent_id: str,
        task_id: str,
    ) -> ExecutionResult:
        """Record costs, apply transitions, run recovery and classify."""
        await record_execution_costs(
            execution_result,
            identity,
            agent_id,
            task_id,
            tracker=self._cost_tracker,
        )
        execution_result = self._apply_post_execution_transitions(
            execution_result,
            agent_id,
            task_id,
        )
        if execution_result.termination_reason == TerminationReason.ERROR:
            execution_result = await self._apply_recovery(
                execution_result,
                agent_id,
                task_id,
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
            except Exception:
                logger.debug(
                    EXECUTION_ENGINE_ERROR,
                    agent_id=agent_id,
                    task_id=task_id,
                    error="classification failed (details logged by pipeline)",
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
        coro = self._loop.execute(
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

    def _prepare_context(  # noqa: PLR0913
        self,
        *,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        max_turns: int,
        memory_messages: tuple[ChatMessage, ...],
        tool_invoker: ToolInvoker | None = None,
    ) -> tuple[AgentContext, SystemPrompt]:
        """Build system prompt and prepare execution context."""
        tool_defs = self._get_tool_definitions(tool_invoker)
        system_prompt = build_system_prompt(
            agent=identity,
            task=task,
            available_tools=tool_defs,
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

        ctx = self._transition_task_if_needed(ctx, agent_id, task_id)
        return ctx, system_prompt

    # ── Helpers ──────────────────────────────────────────────────

    def _get_tool_definitions(
        self,
        tool_invoker: ToolInvoker | None,
    ) -> tuple[ToolDefinition, ...]:
        """Extract permitted tool definitions for prompt building."""
        if tool_invoker is None:
            return ()
        return tool_invoker.get_permitted_definitions()

    def _transition_task_if_needed(
        self,
        ctx: AgentContext,
        agent_id: str,
        task_id: str,
    ) -> AgentContext:
        """Transition ASSIGNED -> IN_PROGRESS; pass through IN_PROGRESS."""
        if (
            ctx.task_execution is not None
            and ctx.task_execution.status == TaskStatus.ASSIGNED
        ):
            ctx = ctx.with_task_transition(
                TaskStatus.IN_PROGRESS,
                reason="Engine starting execution",
            )
            logger.info(
                EXECUTION_ENGINE_TASK_TRANSITION,
                agent_id=agent_id,
                task_id=task_id,
                from_status=TaskStatus.ASSIGNED.value,
                to_status=TaskStatus.IN_PROGRESS.value,
            )
        return ctx

    def _apply_post_execution_transitions(
        self,
        execution_result: ExecutionResult,
        agent_id: str,
        task_id: str,
    ) -> ExecutionResult:
        """Apply post-execution task transitions based on termination reason.

        COMPLETED triggers IN_PROGRESS -> IN_REVIEW -> COMPLETED.
        SHUTDOWN triggers current status -> INTERRUPTED.
        Transition failures are logged but never discard the result.
        """
        ctx = execution_result.context
        if ctx.task_execution is None:
            return execution_result

        reason = execution_result.termination_reason

        if reason == TerminationReason.SHUTDOWN:
            return self._transition_to_interrupted(
                execution_result, ctx, agent_id, task_id
            )

        if reason != TerminationReason.COMPLETED:
            return execution_result

        try:
            ctx = self._transition_to_complete(ctx, agent_id, task_id)
        except (ValueError, ExecutionStateError) as exc:
            logger.exception(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error=f"Post-execution transition failed: {exc}",
            )
            return execution_result

        return execution_result.model_copy(update={"context": ctx})

    def _transition_to_complete(
        self,
        ctx: AgentContext,
        agent_id: str,
        task_id: str,
    ) -> AgentContext:
        """Transition IN_PROGRESS -> IN_REVIEW -> COMPLETED with logging."""
        prev_status = ctx.task_execution.status  # type: ignore[union-attr]
        ctx = ctx.with_task_transition(
            TaskStatus.IN_REVIEW,
            reason="Agent completed execution",
        )
        logger.info(
            EXECUTION_ENGINE_TASK_TRANSITION,
            agent_id=agent_id,
            task_id=task_id,
            from_status=prev_status.value,
            to_status=TaskStatus.IN_REVIEW.value,
        )
        # TODO(M4): Replace auto-complete with review gate (§6.5)
        prev_status = ctx.task_execution.status  # type: ignore[union-attr]
        ctx = ctx.with_task_transition(
            TaskStatus.COMPLETED,
            reason="Auto-completed (no reviewers in M3)",
        )
        logger.info(
            EXECUTION_ENGINE_TASK_TRANSITION,
            agent_id=agent_id,
            task_id=task_id,
            from_status=prev_status.value,
            to_status=TaskStatus.COMPLETED.value,
        )
        return ctx

    def _transition_to_interrupted(
        self,
        execution_result: ExecutionResult,
        ctx: AgentContext,
        agent_id: str,
        task_id: str,
    ) -> ExecutionResult:
        """Transition task to INTERRUPTED on graceful shutdown."""
        try:
            prev_status = ctx.task_execution.status  # type: ignore[union-attr]
            ctx = ctx.with_task_transition(
                TaskStatus.INTERRUPTED,
                reason="Graceful shutdown requested",
            )
            logger.info(
                EXECUTION_ENGINE_TASK_TRANSITION,
                agent_id=agent_id,
                task_id=task_id,
                from_status=prev_status.value,
                to_status=TaskStatus.INTERRUPTED.value,
            )
            return execution_result.model_copy(update={"context": ctx})
        except (ValueError, ExecutionStateError) as exc:
            logger.exception(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error=f"Post-execution INTERRUPTED transition failed: {exc}",
            )
            return execution_result

    async def _apply_recovery(
        self,
        execution_result: ExecutionResult,
        agent_id: str,
        task_id: str,
    ) -> ExecutionResult:
        """Invoke the configured recovery strategy on error outcomes.

        The default strategy transitions the task to FAILED; other
        strategies may behave differently.  If no strategy is set or
        no task execution exists, returns the result unchanged.
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

    def _make_tool_invoker(
        self,
        identity: AgentIdentity,
    ) -> ToolInvoker | None:
        """Create a ToolInvoker with permission checking, or None."""
        if self._tool_registry is None:
            return None
        checker = ToolPermissionChecker.from_permissions(identity.tools)
        return ToolInvoker(self._tool_registry, permission_checker=checker)

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
        )

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

        try:
            error_execution = await self._build_error_execution(
                identity,
                task,
                agent_id,
                task_id,
                error_msg,
                ctx,
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
            logger.error(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error="non-recoverable error while building error result",
                exc_info=True,
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
            raise exc from build_exc

    async def _build_error_execution(  # noqa: PLR0913
        self,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        error_msg: str,
        ctx: AgentContext | None,
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
            agent_id,
            task_id,
        )
