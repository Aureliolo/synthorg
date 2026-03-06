"""Agent engine — top-level orchestrator.

Ties together prompt construction, execution context, execution loop,
tool invocation, and budget tracking into a single ``run()`` entry point.
"""

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
)
from ai_company.engine.prompt import SystemPrompt, build_system_prompt
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
    EXECUTION_ENGINE_TASK_TRANSITION,
)
from ai_company.providers.enums import MessageRole
from ai_company.providers.models import ChatMessage
from ai_company.tools.invoker import ToolInvoker

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
"""Task statuses the engine will accept for execution."""


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

    async def run(
        self,
        *,
        identity: AgentIdentity,
        task: Task,
        completion_config: CompletionConfig | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
    ) -> AgentRunResult:
        """Execute an agent on a task.

        Args:
            identity: Frozen agent identity card.
            task: Task to execute (must be ASSIGNED or IN_PROGRESS).
            completion_config: Optional per-run LLM config override.
            max_turns: Maximum LLM turns allowed.

        Returns:
            ``AgentRunResult`` with execution outcome and metadata.

        Raises:
            ExecutionStateError: If the agent is not ACTIVE or the task
                is not ASSIGNED/IN_PROGRESS.
            MemoryError: Re-raised unconditionally (non-recoverable).
            RecursionError: Re-raised unconditionally (non-recoverable).
        """
        agent_id = str(identity.id)
        task_id = task.id

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
        try:
            return await self._execute(
                identity=identity,
                task=task,
                agent_id=agent_id,
                task_id=task_id,
                completion_config=completion_config,
                max_turns=max_turns,
                start=start,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            return self._handle_fatal_error(
                exc=exc,
                identity=identity,
                task=task,
                agent_id=agent_id,
                task_id=task_id,
                duration_seconds=time.monotonic() - start,
            )

    async def _execute(  # noqa: PLR0913
        self,
        *,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        completion_config: CompletionConfig | None,
        max_turns: int,
        start: float,
    ) -> AgentRunResult:
        """Run loop, record costs, return result."""
        ctx, system_prompt = self._prepare_context(
            identity=identity,
            task=task,
            agent_id=agent_id,
            task_id=task_id,
            max_turns=max_turns,
        )
        budget_checker = _make_budget_checker(task)
        tool_invoker = self._make_tool_invoker()

        logger.debug(
            EXECUTION_ENGINE_PROMPT_BUILT,
            agent_id=agent_id,
            task_id=task_id,
            estimated_tokens=system_prompt.estimated_tokens,
        )

        execution_result = await self._loop.execute(
            context=ctx,
            provider=self._provider,
            tool_invoker=tool_invoker,
            budget_checker=budget_checker,
            completion_config=completion_config,
        )
        duration = time.monotonic() - start

        await self._record_costs(
            execution_result,
            identity,
            agent_id,
            task_id,
        )

        result = AgentRunResult(
            execution_result=execution_result,
            system_prompt=system_prompt,
            duration_seconds=duration,
            agent_id=agent_id,
            task_id=task_id,
        )
        logger.info(
            EXECUTION_ENGINE_COMPLETE,
            agent_id=agent_id,
            task_id=task_id,
            termination_reason=result.termination_reason.value,
            total_turns=result.total_turns,
            total_tokens=execution_result.context.accumulated_cost.total_tokens,
            duration_seconds=duration,
            cost_usd=result.total_cost_usd,
        )
        return result

    # ── Setup ────────────────────────────────────────────────────

    def _prepare_context(
        self,
        *,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        max_turns: int,
    ) -> tuple[AgentContext, SystemPrompt]:
        """Build system prompt and prepare execution context."""
        tool_defs = self._get_tool_definitions()
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
        # Seed conversation with system prompt and task instruction
        ctx = ctx.with_message(
            ChatMessage(role=MessageRole.SYSTEM, content=system_prompt.content),
        )
        ctx = ctx.with_message(
            ChatMessage(
                role=MessageRole.USER,
                content=_format_task_instruction(task),
            ),
        )

        ctx = self._transition_task_if_needed(ctx, agent_id, task_id)
        return ctx, system_prompt

    # ── Validation ───────────────────────────────────────────────

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
        """Raise if task is not in an executable status."""
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

    # ── Helpers ──────────────────────────────────────────────────

    def _get_tool_definitions(self) -> tuple[ToolDefinition, ...]:
        """Extract tool definitions from the registry for prompt building."""
        if self._tool_registry is None:
            return ()
        return self._tool_registry.to_definitions()

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

    def _make_tool_invoker(self) -> ToolInvoker | None:
        """Create a ToolInvoker from the registry, or None."""
        if self._tool_registry is None:
            return None
        return ToolInvoker(self._tool_registry)

    async def _record_costs(
        self,
        result: ExecutionResult,
        identity: AgentIdentity,
        agent_id: str,
        task_id: str,
    ) -> None:
        """Record accumulated costs to the CostTracker if available.

        Cost recording failures are logged but do not affect the
        execution result — a successful run is never downgraded to
        an error because of a recording failure.
        """
        if self._cost_tracker is None:
            return

        usage = result.context.accumulated_cost
        if usage.cost_usd <= 0.0 and usage.input_tokens == 0:
            logger.debug(
                EXECUTION_ENGINE_COST_SKIPPED,
                agent_id=agent_id,
                task_id=task_id,
                reason="zero cost and zero input tokens",
            )
            return

        try:
            record = CostRecord(
                agent_id=agent_id,
                task_id=task_id,
                provider=identity.model.provider,
                model=identity.model.model_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=usage.cost_usd,
                timestamp=datetime.now(UTC),
            )
            await self._cost_tracker.record(record)
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                EXECUTION_ENGINE_COST_FAILED,
                agent_id=agent_id,
                task_id=task_id,
            )
            return

        logger.info(
            EXECUTION_ENGINE_COST_RECORDED,
            agent_id=agent_id,
            task_id=task_id,
            cost_usd=usage.cost_usd,
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
    ) -> AgentRunResult:
        """Build an error result from an unexpected exception."""
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.exception(
            EXECUTION_ENGINE_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            error=error_msg,
        )

        ctx = AgentContext.from_identity(identity, task=task)
        error_execution = ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.ERROR,
            error_message=error_msg,
        )
        error_prompt = SystemPrompt(
            content="",
            template_version="error",
            estimated_tokens=0,
            sections=(),
            metadata={"agent_id": agent_id},
        )
        return AgentRunResult(
            execution_result=error_execution,
            system_prompt=error_prompt,
            duration_seconds=duration_seconds,
            agent_id=agent_id,
            task_id=task_id,
        )


def _format_task_instruction(task: Task) -> str:
    """Format a task into a user message for the initial conversation."""
    parts = [f"# Task: {task.title}", "", task.description]

    if task.acceptance_criteria:
        parts.append("")
        parts.append("## Acceptance Criteria")
        parts.extend(f"- {c.description}" for c in task.acceptance_criteria)

    if task.budget_limit > 0:
        parts.append("")
        parts.append(f"**Budget limit:** ${task.budget_limit:.2f} USD")

    if task.deadline:
        parts.append(f"**Deadline:** {task.deadline}")

    return "\n".join(parts)


def _make_budget_checker(task: Task) -> BudgetChecker | None:
    """Create a budget checker if the task has a positive budget limit."""
    if task.budget_limit <= 0:
        return None

    limit = task.budget_limit

    def _check(ctx: AgentContext) -> bool:
        return ctx.accumulated_cost.cost_usd >= limit

    return _check
