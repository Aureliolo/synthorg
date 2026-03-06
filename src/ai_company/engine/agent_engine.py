"""Agent engine — top-level orchestrator.

Ties together prompt construction, execution context, execution loop,
tool invocation, and budget tracking into a single ``run()`` entry point.
"""

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ai_company.budget.cost_record import CostRecord
from ai_company.core.enums import AgentStatus, TaskStatus
from ai_company.engine.context import AgentContext
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
    EXECUTION_ENGINE_COST_RECORDED,
    EXECUTION_ENGINE_ERROR,
    EXECUTION_ENGINE_INVALID_INPUT,
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

DEFAULT_MAX_TURNS: int = 20
"""Default hard limit on LLM turns per agent engine run."""

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
        cost_tracker: Optional cost recording service.
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
        """
        agent_id = str(identity.id)
        task_id = task.id

        # 1. Validate inputs
        self._validate_agent(identity, agent_id)
        self._validate_task(task, agent_id, task_id)

        logger.info(
            EXECUTION_ENGINE_START,
            agent_id=agent_id,
            task_id=task_id,
            loop_type=self._loop.get_loop_type(),
            max_turns=max_turns,
        )

        try:
            return await self._execute(
                identity=identity,
                task=task,
                agent_id=agent_id,
                task_id=task_id,
                completion_config=completion_config,
                max_turns=max_turns,
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
    ) -> AgentRunResult:
        """Core execution flow after validation."""
        # 2. Build system prompt
        tool_defs = self._get_tool_definitions()
        system_prompt = build_system_prompt(
            agent=identity,
            task=task,
            available_tools=tool_defs,
        )

        # 3. Create context
        ctx = AgentContext.from_identity(
            identity,
            task=task,
            max_turns=max_turns,
        )

        # 4. Inject working memory (system + user messages)
        ctx = ctx.with_message(
            ChatMessage(role=MessageRole.SYSTEM, content=system_prompt.content),
        )
        ctx = ctx.with_message(
            ChatMessage(role=MessageRole.USER, content=_format_task_instruction(task)),
        )

        # 5. Transition task ASSIGNED -> IN_PROGRESS
        ctx = self._transition_task_if_needed(ctx, agent_id, task_id)

        # 6. Create budget checker
        budget_checker = _make_budget_checker(task)

        # 7. Create tool invoker
        tool_invoker = self._make_tool_invoker()

        # 8. Record start time
        start = time.monotonic()

        # 9. Delegate to loop
        execution_result = await self._loop.execute(
            context=ctx,
            provider=self._provider,
            tool_invoker=tool_invoker,
            budget_checker=budget_checker,
            completion_config=completion_config,
        )

        duration = time.monotonic() - start

        # 10. Record costs
        await self._record_costs(execution_result, identity, task)

        # 11. Build result
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
            duration_seconds=duration,
            cost_usd=result.total_cost_usd,
        )
        return result

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
        task: Task,
    ) -> None:
        """Record accumulated costs to the CostTracker if available."""
        if self._cost_tracker is None:
            return

        usage = result.context.accumulated_cost
        if usage.cost_usd <= 0.0 and usage.input_tokens == 0:
            return

        record = CostRecord(
            agent_id=str(identity.id),
            task_id=task.id,
            provider=identity.model.provider,
            model=identity.model.model_id,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=usage.cost_usd,
            timestamp=datetime.now(UTC),
        )
        await self._cost_tracker.record(record)
        logger.info(
            EXECUTION_ENGINE_COST_RECORDED,
            agent_id=str(identity.id),
            task_id=task.id,
            cost_usd=usage.cost_usd,
        )

    def _handle_fatal_error(
        self,
        *,
        exc: Exception,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
    ) -> AgentRunResult:
        """Catch unexpected errors and return an error result."""
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.exception(
            EXECUTION_ENGINE_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            error=error_msg,
        )

        # Build a minimal error result
        ctx = AgentContext.from_identity(identity, task=task)
        error_execution = ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.ERROR,
            error_message=error_msg,
        )
        # Minimal system prompt for the error result
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
            duration_seconds=0.0,
            agent_id=agent_id,
            task_id=task_id,
        )


def _format_task_instruction(task: Task) -> str:
    """Format a task into a user message for the initial conversation.

    Args:
        task: The task to format.

    Returns:
        Formatted task instruction string.
    """
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
    """Create a budget checker callback if the task has a budget limit.

    Args:
        task: Task with optional budget_limit.

    Returns:
        Budget checker callable or None.
    """
    if task.budget_limit <= 0:
        return None

    limit = task.budget_limit

    def _check(ctx: AgentContext) -> bool:
        return ctx.accumulated_cost.cost_usd >= limit

    return _check
