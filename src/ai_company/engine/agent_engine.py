"""Agent engine — top-level orchestrator.

Ties together prompt construction, execution context, execution loop,
tool invocation, and budget tracking into a single ``run()`` entry point.
"""

import asyncio
import contextlib
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ai_company.budget.cost_record import CostRecord
from ai_company.core.enums import AgentStatus, TaskStatus
from ai_company.engine.context import DEFAULT_MAX_TURNS, AgentContext
from ai_company.engine.errors import ExecutionStateError
from ai_company.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
    TurnRecord,
)
from ai_company.engine.metrics import TaskCompletionMetrics
from ai_company.engine.prompt import (
    SystemPrompt,
    build_system_prompt,
    format_task_instruction,
)
from ai_company.engine.react_loop import ReactLoop
from ai_company.engine.run_result import AgentRunResult
from ai_company.observability import get_logger
from ai_company.observability.events.execution import (
    EXECUTION_ENGINE_COMPLETE,
    EXECUTION_ENGINE_COST_FAILED,
    EXECUTION_ENGINE_COST_RECORDED,
    EXECUTION_ENGINE_COST_SKIPPED,
    EXECUTION_ENGINE_CREATED,
    EXECUTION_ENGINE_ERROR,
    EXECUTION_ENGINE_INVALID_INPUT,
    EXECUTION_ENGINE_PROMPT_BUILT,
    EXECUTION_ENGINE_START,
    EXECUTION_ENGINE_TASK_METRICS,
    EXECUTION_ENGINE_TASK_TRANSITION,
    EXECUTION_ENGINE_TIMEOUT,
)
from ai_company.providers.enums import MessageRole
from ai_company.providers.models import ChatMessage
from ai_company.tools.invoker import ToolInvoker
from ai_company.tools.permissions import ToolPermissionChecker

if TYPE_CHECKING:
    from ai_company.budget.tracker import CostTracker
    from ai_company.core.agent import AgentIdentity
    from ai_company.core.task import Task
    from ai_company.engine.loop_protocol import BudgetChecker, ExecutionLoop
    from ai_company.providers.models import CompletionConfig, ToolDefinition
    from ai_company.providers.protocol import CompletionProvider
    from ai_company.tools.registry import ToolRegistry

logger = get_logger(__name__)

_EXECUTABLE_STATUSES = frozenset({TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS})
"""Task statuses the engine will accept for execution.

CREATED tasks lack an assignee; terminal statuses (COMPLETED, CANCELLED)
and BLOCKED/IN_REVIEW are not executable.
"""


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
    """

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        execution_loop: ExecutionLoop | None = None,
        tool_registry: ToolRegistry | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self._provider = provider
        self._loop: ExecutionLoop = execution_loop or ReactLoop()
        self._tool_registry = tool_registry
        self._cost_tracker = cost_tracker
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

        Args:
            identity: Frozen agent identity card.
            task: Task to execute (must be ASSIGNED or IN_PROGRESS).
            completion_config: Optional per-run LLM config override.
            max_turns: Maximum LLM turns allowed (must be >= 1).
            memory_messages: Optional working memory messages to inject
                between the system prompt and task instruction.
            timeout_seconds: Optional wall-clock timeout in seconds.
                When exceeded, the execution loop is cancelled and the
                run returns with ``TerminationReason.ERROR``. Cost
                recording and post-execution processing still occur.

        Returns:
            ``AgentRunResult`` with execution outcome and metadata.
            All exceptions during execution (other than those listed
            below) are caught and returned as an error result rather
            than propagated.

        Raises:
            ExecutionStateError: If pre-flight validation fails (agent
                not ACTIVE or task not ASSIGNED/IN_PROGRESS).
            ValueError: If ``max_turns`` is less than 1, or if
                ``timeout_seconds`` is not positive.
            MemoryError: Re-raised unconditionally (non-recoverable).
            RecursionError: Re-raised unconditionally (non-recoverable).
        """
        agent_id = str(identity.id)
        task_id = task.id

        self._validate_run_inputs(
            agent_id=agent_id,
            task_id=task_id,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
        )
        self._validate_agent(identity, agent_id)
        self._validate_task(task, agent_id, task_id)

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
            return self._handle_fatal_error(
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
        """Run execution loop, record costs, apply transitions, and build result.

        Orchestrates the full execution pipeline: loop execution (with
        optional wall-clock timeout via ``asyncio.wait``), per-turn cost
        recording, post-execution task transitions, and metrics logging.
        """
        budget_checker = _make_budget_checker(task)

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

        await self._record_costs(execution_result, identity, agent_id, task_id)
        execution_result = self._apply_post_execution_transitions(
            execution_result,
            agent_id,
            task_id,
        )

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

    # ── Validation ───────────────────────────────────────────────

    def _validate_run_inputs(
        self,
        *,
        agent_id: str,
        task_id: str,
        max_turns: int,
        timeout_seconds: float | None,
    ) -> None:
        """Validate scalar ``run()`` arguments before execution."""
        if max_turns < 1:
            msg = f"max_turns must be >= 1, got {max_turns}"
            logger.warning(
                EXECUTION_ENGINE_INVALID_INPUT,
                agent_id=agent_id,
                task_id=task_id,
                reason=msg,
            )
            raise ValueError(msg)
        if timeout_seconds is not None and timeout_seconds <= 0:
            msg = f"timeout_seconds must be > 0, got {timeout_seconds}"
            logger.warning(
                EXECUTION_ENGINE_INVALID_INPUT,
                agent_id=agent_id,
                task_id=task_id,
                reason=msg,
            )
            raise ValueError(msg)

    def _validate_agent(self, identity: AgentIdentity, agent_id: str) -> None:
        """Raise if agent is not ACTIVE."""
        if identity.status != AgentStatus.ACTIVE:
            msg = (
                f"Agent {agent_id} has status {identity.status.value!r}; "
                f"only 'active' agents can run tasks"
            )
            logger.warning(
                EXECUTION_ENGINE_INVALID_INPUT,
                agent_id=agent_id,
                reason=msg,
            )
            raise ExecutionStateError(msg)

    def _validate_task(
        self,
        task: Task,
        agent_id: str,
        task_id: str,
    ) -> None:
        """Raise if task is not executable or not assigned to this agent."""
        if task.status not in _EXECUTABLE_STATUSES:
            msg = (
                f"Task {task_id!r} has status {task.status.value!r}; "
                f"only 'assigned' or 'in_progress' tasks can be executed"
            )
            logger.warning(
                EXECUTION_ENGINE_INVALID_INPUT,
                agent_id=agent_id,
                task_id=task_id,
                reason=msg,
            )
            raise ExecutionStateError(msg)
        if task.assigned_to is not None and task.assigned_to != agent_id:
            msg = (
                f"Task {task_id!r} is assigned to {task.assigned_to!r}, "
                f"not to agent {agent_id!r}"
            )
            logger.warning(
                EXECUTION_ENGINE_INVALID_INPUT,
                agent_id=agent_id,
                task_id=task_id,
                reason=msg,
            )
            raise ExecutionStateError(msg)

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

        Only ``TerminationReason.COMPLETED`` triggers transitions:
        IN_PROGRESS → IN_REVIEW → COMPLETED (two-hop auto-complete).
        All other reasons leave the task in its current state.

        Note:
            The IN_REVIEW → COMPLETED auto-complete is M3 scaffolding
            (no reviewers yet). Later milestones will gate COMPLETED
            on reviewer approval.

        Transition failures are logged but do not discard the
        successful execution result — a bookkeeping error must never
        destroy the agent's work.

        Args:
            execution_result: Result from the execution loop.
            agent_id: Agent identifier for logging.
            task_id: Task identifier for logging.

        Returns:
            New ``ExecutionResult`` with updated context if transitions
            were applied, or the original result unchanged.
        """
        ctx = execution_result.context
        if ctx.task_execution is None:
            return execution_result

        if execution_result.termination_reason != TerminationReason.COMPLETED:
            return execution_result

        try:
            prev_status = ctx.task_execution.status
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
            # TODO(M4): Replace auto-complete with review gate
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
        except (ValueError, ExecutionStateError) as exc:
            logger.exception(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error=f"Post-execution transition failed: {exc}",
            )
            return execution_result

        return execution_result.model_copy(update={"context": ctx})

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

    async def _record_costs(
        self,
        result: ExecutionResult,
        identity: AgentIdentity,
        agent_id: str,
        task_id: str,
    ) -> None:
        """Record per-turn costs to the CostTracker if available.

        Each turn produces its own ``CostRecord``, preserving per-call
        granularity. Turns with zero cost and zero tokens are skipped.

        Recording failures for regular exceptions are logged but do not
        affect the execution result. ``MemoryError`` and
        ``RecursionError`` propagate unconditionally as non-recoverable
        system errors.
        """
        if self._cost_tracker is None:
            logger.debug(
                EXECUTION_ENGINE_COST_SKIPPED,
                agent_id=agent_id,
                task_id=task_id,
                reason="no cost tracker configured",
            )
            return

        tracker = self._cost_tracker

        for turn in result.turns:
            # Skip only when provably nothing happened (zero cost and
            # zero tokens); a turn with tokens but zero cost (e.g., a
            # free-tier provider) is still recorded.
            if (
                turn.cost_usd <= 0.0
                and turn.input_tokens == 0
                and turn.output_tokens == 0
            ):
                logger.debug(
                    EXECUTION_ENGINE_COST_SKIPPED,
                    agent_id=agent_id,
                    task_id=task_id,
                    turn_number=turn.turn_number,
                    reason="zero cost and zero tokens",
                )
                continue

            record = CostRecord(
                agent_id=agent_id,
                task_id=task_id,
                provider=identity.model.provider,
                model=identity.model.model_id,
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
                cost_usd=turn.cost_usd,
                timestamp=datetime.now(UTC),
            )
            await self._submit_cost(
                record,
                turn,
                agent_id,
                task_id,
                tracker=tracker,
            )

    async def _submit_cost(
        self,
        record: CostRecord,
        turn: TurnRecord,
        agent_id: str,
        task_id: str,
        *,
        tracker: CostTracker,
    ) -> None:
        """Submit a cost record to the tracker, logging failures."""
        try:
            await tracker.record(record)
        except MemoryError, RecursionError:
            logger.error(
                EXECUTION_ENGINE_COST_FAILED,
                agent_id=agent_id,
                task_id=task_id,
                error="non-recoverable error in cost recording",
                exc_info=True,
            )
            raise
        except Exception as exc:
            logger.exception(
                EXECUTION_ENGINE_COST_FAILED,
                agent_id=agent_id,
                task_id=task_id,
                error=f"{type(exc).__name__}: {exc}",
                cost_usd=turn.cost_usd,
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
            )
            return

        logger.info(
            EXECUTION_ENGINE_COST_RECORDED,
            agent_id=agent_id,
            task_id=task_id,
            cost_usd=turn.cost_usd,
        )

    def _handle_fatal_error(  # noqa: PLR0913
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

        When ``ctx`` and ``system_prompt`` are provided (i.e. context
        preparation succeeded before the failure), they are preserved in
        the error result so that accumulated state (conversation, task
        transition) is not lost.

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
            error_ctx = ctx or AgentContext.from_identity(identity, task=task)
            error_execution = ExecutionResult(
                context=error_ctx,
                termination_reason=TerminationReason.ERROR,
                error_message=error_msg,
            )
            error_prompt = system_prompt or SystemPrompt(
                content="",
                template_version="error",
                estimated_tokens=0,
                sections=(),
                metadata={
                    "agent_id": agent_id,
                    "name": identity.name,
                    "role": identity.role,
                    "department": identity.department,
                    "level": identity.level.value,
                },
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
            raise exc from None


def _make_budget_checker(task: Task) -> BudgetChecker | None:
    """Create a budget checker if the task has a positive budget limit.

    The returned callable returns ``True`` when accumulated cost meets
    or exceeds the limit (budget exhausted), ``False`` otherwise.
    Returns ``None`` when there is no positive budget limit.
    """
    if task.budget_limit <= 0:
        return None

    limit = task.budget_limit

    def _check(ctx: AgentContext) -> bool:
        return ctx.accumulated_cost.cost_usd >= limit

    return _check
