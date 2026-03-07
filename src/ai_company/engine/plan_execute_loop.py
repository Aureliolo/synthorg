"""Plan-and-Execute execution loop.

Implements the ``ExecutionLoop`` protocol using a two-phase approach:
1. **Plan** — ask the LLM to decompose the task into ordered steps.
   Planning calls pass ``tools=None`` (no tool access during planning).
2. **Execute** — run each step via a mini-ReAct sub-loop with tools.

Re-planning is triggered when a step fails, up to a configurable
limit.  When re-planning is exhausted, the loop terminates with ERROR.
"""

import copy
import json
import re
from typing import TYPE_CHECKING

from ai_company.observability import get_logger
from ai_company.observability.events.execution import (
    EXECUTION_LOOP_START,
    EXECUTION_LOOP_TERMINATED,
    EXECUTION_LOOP_TURN_COMPLETE,
    EXECUTION_PLAN_CREATED,
    EXECUTION_PLAN_PARSE_ERROR,
    EXECUTION_PLAN_REPLAN_COMPLETE,
    EXECUTION_PLAN_REPLAN_EXHAUSTED,
    EXECUTION_PLAN_REPLAN_START,
    EXECUTION_PLAN_STEP_COMPLETE,
    EXECUTION_PLAN_STEP_FAILED,
    EXECUTION_PLAN_STEP_START,
)
from ai_company.providers.enums import FinishReason, MessageRole
from ai_company.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
)

from .loop_helpers import (
    build_result,
    call_provider,
    check_budget,
    check_response_errors,
    check_shutdown,
    clear_last_turn_tool_calls,
    execute_tool_calls,
    get_tool_definitions,
    make_turn_record,
    response_to_message,
)
from .loop_protocol import (
    BudgetChecker,
    ExecutionResult,
    ShutdownChecker,
    TerminationReason,
    TurnRecord,
)
from .plan_models import (
    ExecutionPlan,
    PlanExecuteConfig,
    PlanStep,
    StepStatus,
)

if TYPE_CHECKING:
    from ai_company.engine.context import AgentContext
    from ai_company.providers.models import ToolDefinition
    from ai_company.providers.protocol import CompletionProvider
    from ai_company.tools.invoker import ToolInvoker

logger = get_logger(__name__)

_PLANNING_PROMPT = """\
You are a planning agent. Analyze the task and create a step-by-step \
execution plan. Return your plan as a JSON object with this exact schema:

```json
{
  "steps": [
    {
      "step_number": 1,
      "description": "What to do in this step",
      "expected_outcome": "What should result from this step"
    }
  ]
}
```

Each step should be concrete, actionable, and independently verifiable. \
Return ONLY the JSON object, no other text."""

_REPLAN_JSON_EXAMPLE = """\
```json
{
  "steps": [
    {
      "step_number": 1,
      "description": "What to do in this step",
      "expected_outcome": "What should result from this step"
    }
  ]
}
```"""


class PlanExecuteLoop:
    """Plan-and-Execute execution loop.

    Decomposes a task into steps via LLM planning, then executes each
    step with a mini-ReAct sub-loop. Supports re-planning on failure.
    """

    def __init__(self, config: PlanExecuteConfig | None = None) -> None:
        self._config = config or PlanExecuteConfig()

    def get_loop_type(self) -> str:
        """Return the loop type identifier."""
        return "plan_execute"

    async def execute(  # noqa: PLR0913
        self,
        *,
        context: AgentContext,
        provider: CompletionProvider,
        tool_invoker: ToolInvoker | None = None,
        budget_checker: BudgetChecker | None = None,
        shutdown_checker: ShutdownChecker | None = None,
        completion_config: CompletionConfig | None = None,
    ) -> ExecutionResult:
        """Run the Plan-and-Execute loop until termination.

        Args:
            context: Initial agent context with conversation.
            provider: LLM completion provider.
            tool_invoker: Optional tool invoker for tool execution.
            budget_checker: Optional budget exhaustion callback.
            shutdown_checker: Optional callback; returns ``True`` when
                a graceful shutdown has been requested.
            completion_config: Optional per-execution config override.

        Returns:
            Execution result with final context and termination info.

        Raises:
            MemoryError: Re-raised unconditionally (non-recoverable).
            RecursionError: Re-raised unconditionally (non-recoverable).
        """
        logger.info(
            EXECUTION_LOOP_START,
            execution_id=context.execution_id,
            loop_type=self.get_loop_type(),
            max_turns=context.max_turns,
        )

        ctx = context
        default_model = ctx.identity.model.model_id
        planner_model = self._config.planner_model or default_model
        executor_model = self._config.executor_model or default_model
        default_config = completion_config or CompletionConfig(
            temperature=ctx.identity.model.temperature,
            max_tokens=ctx.identity.model.max_tokens,
        )
        tool_defs = get_tool_definitions(tool_invoker)
        turns: list[TurnRecord] = []
        all_plans: list[ExecutionPlan] = []
        replans_used = 0

        # Phase 1: Planning
        plan_result = await self._run_planning_phase(
            ctx,
            provider,
            planner_model,
            default_config,
            turns,
            shutdown_checker,
            budget_checker,
        )
        if isinstance(plan_result, ExecutionResult):
            return plan_result
        ctx, plan = plan_result
        all_plans.append(plan)

        # Phase 2: Execute steps
        return await self._run_steps(
            ctx,
            provider,
            executor_model,
            default_config,
            tool_defs,
            tool_invoker,
            plan,
            turns,
            all_plans,
            replans_used,
            planner_model,
            budget_checker,
            shutdown_checker,
        )

    # ── Phase orchestration ─────────────────────────────────────────

    async def _run_planning_phase(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        planner_model: str,
        config: CompletionConfig,
        turns: list[TurnRecord],
        shutdown_checker: ShutdownChecker | None,
        budget_checker: BudgetChecker | None,
    ) -> tuple[AgentContext, ExecutionPlan] | ExecutionResult:
        """Run pre-checks and generate the initial plan."""
        shutdown_result = check_shutdown(ctx, shutdown_checker, turns)
        if shutdown_result is not None:
            return shutdown_result
        budget_result = check_budget(ctx, budget_checker, turns)
        if budget_result is not None:
            return budget_result
        return await self._generate_plan(
            ctx,
            provider,
            planner_model,
            config,
            turns,
        )

    async def _run_steps(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        executor_model: str,
        config: CompletionConfig,
        tool_defs: list[ToolDefinition] | None,
        tool_invoker: ToolInvoker | None,
        plan: ExecutionPlan,
        turns: list[TurnRecord],
        all_plans: list[ExecutionPlan],
        replans_used: int,
        planner_model: str,
        budget_checker: BudgetChecker | None,
        shutdown_checker: ShutdownChecker | None,
    ) -> ExecutionResult:
        """Iterate through plan steps, handling failures and replanning."""
        step_idx = 0
        while step_idx < len(plan.steps):
            if not ctx.has_turns_remaining:
                break

            step = plan.steps[step_idx]
            logger.info(
                EXECUTION_PLAN_STEP_START,
                execution_id=ctx.execution_id,
                step_number=step.step_number,
                description=step.description,
            )

            step_result = await self._execute_step(
                ctx,
                provider,
                executor_model,
                config,
                tool_defs,
                tool_invoker,
                step,
                turns,
                budget_checker,
                shutdown_checker,
            )

            if isinstance(step_result, ExecutionResult):
                return self._finalize(step_result, all_plans, replans_used)

            ctx, step_ok = step_result

            if step_ok:
                plan = self._update_step_status(
                    plan,
                    step_idx,
                    StepStatus.COMPLETED,
                )
                logger.info(
                    EXECUTION_PLAN_STEP_COMPLETE,
                    execution_id=ctx.execution_id,
                    step_number=step.step_number,
                )
                step_idx += 1
                continue

            # Step failed — attempt re-planning
            failure = self._handle_step_failure(
                ctx,
                plan,
                step,
                step_idx,
                replans_used,
                turns,
            )
            if isinstance(failure, ExecutionResult):
                return self._finalize(failure, all_plans, replans_used)
            plan = failure

            if not ctx.has_turns_remaining:
                break
            replan_result = await self._replan(
                ctx,
                provider,
                planner_model,
                config,
                plan,
                step,
                turns,
            )
            if isinstance(replan_result, ExecutionResult):
                return self._finalize(replan_result, all_plans, replans_used)

            ctx, new_plan = replan_result
            replans_used += 1
            all_plans.append(new_plan)
            plan = new_plan
            step_idx = 0

        return self._build_final_result(
            ctx,
            plan,
            step_idx,
            turns,
            all_plans,
            replans_used,
        )

    def _handle_step_failure(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        plan: ExecutionPlan,
        step: PlanStep,
        step_idx: int,
        replans_used: int,
        turns: list[TurnRecord],
    ) -> ExecutionPlan | ExecutionResult:
        """Mark step as failed and check replan budget.

        Returns:
            Updated ``ExecutionPlan`` if replanning is allowed, or an
            ``ExecutionResult`` if max replans is exhausted.
        """
        plan = self._update_step_status(plan, step_idx, StepStatus.FAILED)
        logger.warning(
            EXECUTION_PLAN_STEP_FAILED,
            execution_id=ctx.execution_id,
            step_number=step.step_number,
        )

        if replans_used >= self._config.max_replans:
            logger.error(
                EXECUTION_PLAN_REPLAN_EXHAUSTED,
                execution_id=ctx.execution_id,
                replans_used=replans_used,
                max_replans=self._config.max_replans,
            )
            error_msg = (
                f"Max replans ({self._config.max_replans}) exhausted "
                f"after step {step.step_number} failed"
            )
            return build_result(
                ctx,
                TerminationReason.ERROR,
                turns,
                error_message=error_msg,
            )
        return plan

    def _build_final_result(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        plan: ExecutionPlan,
        step_idx: int,
        turns: list[TurnRecord],
        all_plans: list[ExecutionPlan],
        replans_used: int,
    ) -> ExecutionResult:
        """Build the final result after step iteration completes."""
        if not ctx.has_turns_remaining and step_idx < len(plan.steps):
            logger.info(
                EXECUTION_LOOP_TERMINATED,
                execution_id=ctx.execution_id,
                reason=TerminationReason.MAX_TURNS.value,
                turns=len(turns),
            )
            return self._finalize(
                build_result(ctx, TerminationReason.MAX_TURNS, turns),
                all_plans,
                replans_used,
            )

        logger.info(
            EXECUTION_LOOP_TERMINATED,
            execution_id=ctx.execution_id,
            reason=TerminationReason.COMPLETED.value,
            turns=len(turns),
        )
        return self._finalize(
            build_result(ctx, TerminationReason.COMPLETED, turns),
            all_plans,
            replans_used,
        )

    # ── Planning ────────────────────────────────────────────────────

    async def _generate_plan(
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        planner_model: str,
        config: CompletionConfig,
        turns: list[TurnRecord],
    ) -> tuple[AgentContext, ExecutionPlan] | ExecutionResult:
        """Generate an execution plan from the LLM."""
        plan_msg = ChatMessage(
            role=MessageRole.USER,
            content=_PLANNING_PROMPT,
        )
        result = await self._call_planner(
            ctx,
            provider,
            planner_model,
            config,
            turns,
            plan_msg,
        )
        if isinstance(result, ExecutionResult):
            return result
        ctx, plan = result
        logger.info(
            EXECUTION_PLAN_CREATED,
            execution_id=ctx.execution_id,
            step_count=len(plan.steps),
            revision=plan.revision_number,
        )
        return ctx, plan

    async def _replan(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        planner_model: str,
        config: CompletionConfig,
        current_plan: ExecutionPlan,
        failed_step: PlanStep,
        turns: list[TurnRecord],
    ) -> tuple[AgentContext, ExecutionPlan] | ExecutionResult:
        """Generate a revised plan after a step failure."""
        logger.info(
            EXECUTION_PLAN_REPLAN_START,
            execution_id=ctx.execution_id,
            failed_step=failed_step.step_number,
            revision=current_plan.revision_number,
        )

        completed_summary = (
            "\n".join(
                f"  Step {s.step_number}: {s.description} -> COMPLETED"
                for s in current_plan.steps
                if s.status == StepStatus.COMPLETED
            )
            or "  (none)"
        )

        replan_content = (
            f"Step {failed_step.step_number} failed: "
            f"{failed_step.description}\n\n"
            f"Completed steps so far:\n{completed_summary}\n\n"
            f"Create a revised plan for the REMAINING work. "
            f"Return your revised plan as a JSON object with the "
            f"same schema:\n\n{_REPLAN_JSON_EXAMPLE}\n\n"
            f"Return ONLY the JSON object, no other text."
        )
        replan_msg = ChatMessage(
            role=MessageRole.USER,
            content=replan_content,
        )
        result = await self._call_planner(
            ctx,
            provider,
            planner_model,
            config,
            turns,
            replan_msg,
            revision_number=current_plan.revision_number + 1,
        )
        if isinstance(result, ExecutionResult):
            return result
        ctx, plan = result
        logger.info(
            EXECUTION_PLAN_REPLAN_COMPLETE,
            execution_id=ctx.execution_id,
            step_count=len(plan.steps),
            revision=plan.revision_number,
        )
        return ctx, plan

    async def _call_planner(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        model: str,
        config: CompletionConfig,
        turns: list[TurnRecord],
        message: ChatMessage,
        *,
        revision_number: int = 0,
    ) -> tuple[AgentContext, ExecutionPlan] | ExecutionResult:
        """Shared body for plan generation and re-planning.

        Sends the message to the LLM, records the turn, parses the
        plan, and returns either ``(ctx, plan)`` or an error result.
        """
        ctx = ctx.with_message(message)
        turn_number = ctx.turn_count + 1

        response = await call_provider(
            ctx,
            provider,
            model,
            None,
            config,
            turn_number,
            turns,
        )
        if isinstance(response, ExecutionResult):
            return response

        turns.append(make_turn_record(turn_number, response))
        ctx = ctx.with_turn_completed(
            response.usage,
            response_to_message(response),
        )
        logger.info(
            EXECUTION_LOOP_TURN_COMPLETE,
            execution_id=ctx.execution_id,
            turn=turn_number,
            finish_reason=response.finish_reason.value,
            tool_call_count=0,
        )

        plan = self._parse_plan(
            response,
            ctx,
            revision_number=revision_number,
        )
        if plan is None:
            error_msg = "Failed to parse execution plan from LLM response"
            return build_result(
                ctx,
                TerminationReason.ERROR,
                turns,
                error_message=error_msg,
            )
        return ctx, plan

    # ── Step execution ──────────────────────────────────────────────

    async def _execute_step(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        executor_model: str,
        config: CompletionConfig,
        tool_defs: list[ToolDefinition] | None,
        tool_invoker: ToolInvoker | None,
        step: PlanStep,
        turns: list[TurnRecord],
        budget_checker: BudgetChecker | None,
        shutdown_checker: ShutdownChecker | None,
    ) -> tuple[AgentContext, bool] | ExecutionResult:
        """Execute a single plan step via a mini-ReAct sub-loop.

        Returns:
            ``(ctx, True)`` on success, ``(ctx, False)`` on step failure,
            or ``ExecutionResult`` for termination conditions.
        """
        instruction = (
            f"Execute step {step.step_number}: {step.description}\n"
            f"Expected outcome: {step.expected_outcome}\n"
            f"When done, respond with a summary of what you accomplished."
        )
        step_msg = ChatMessage(
            role=MessageRole.USER,
            content=instruction,
        )
        ctx = ctx.with_message(step_msg)

        while ctx.has_turns_remaining:
            result = await self._run_step_turn(
                ctx,
                provider,
                executor_model,
                config,
                tool_defs,
                tool_invoker,
                turns,
                budget_checker,
                shutdown_checker,
            )
            if isinstance(result, ExecutionResult):
                return result
            if isinstance(result, tuple):
                return result
            ctx = result

        return ctx, False

    async def _run_step_turn(  # noqa: PLR0911, PLR0913
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        model: str,
        config: CompletionConfig,
        tool_defs: list[ToolDefinition] | None,
        tool_invoker: ToolInvoker | None,
        turns: list[TurnRecord],
        budget_checker: BudgetChecker | None,
        shutdown_checker: ShutdownChecker | None,
    ) -> AgentContext | ExecutionResult | tuple[AgentContext, bool]:
        """Execute a single turn within a step's mini-ReAct sub-loop.

        Returns:
            ``AgentContext`` to continue the loop, ``(ctx, bool)`` for
            step completion, or ``ExecutionResult`` for termination.
        """
        shutdown_result = check_shutdown(ctx, shutdown_checker, turns)
        if shutdown_result is not None:
            return shutdown_result
        budget_result = check_budget(ctx, budget_checker, turns)
        if budget_result is not None:
            return budget_result

        turn_number = ctx.turn_count + 1
        response = await call_provider(
            ctx,
            provider,
            model,
            tool_defs,
            config,
            turn_number,
            turns,
        )
        if isinstance(response, ExecutionResult):
            return response

        turns.append(make_turn_record(turn_number, response))

        error = check_response_errors(ctx, response, turn_number, turns)
        if error is not None:
            return error

        ctx = ctx.with_turn_completed(
            response.usage,
            response_to_message(response),
        )
        logger.info(
            EXECUTION_LOOP_TURN_COMPLETE,
            execution_id=ctx.execution_id,
            turn=turn_number,
            finish_reason=response.finish_reason.value,
            tool_call_count=len(response.tool_calls),
        )

        if not response.tool_calls:
            success = self._assess_step_success(response)
            if response.finish_reason == FinishReason.MAX_TOKENS:
                logger.warning(
                    EXECUTION_PLAN_STEP_COMPLETE,
                    execution_id=ctx.execution_id,
                    turn=turn_number,
                    truncated=True,
                )
            return ctx, success

        shutdown_result = check_shutdown(ctx, shutdown_checker, turns)
        if shutdown_result is not None:
            clear_last_turn_tool_calls(turns)
            return shutdown_result

        return await execute_tool_calls(
            ctx,
            tool_invoker,
            response,
            turn_number,
            turns,
        )

    # ── Plan parsing ────────────────────────────────────────────────

    def _parse_plan(
        self,
        response: CompletionResponse,
        ctx: AgentContext,
        *,
        revision_number: int = 0,
    ) -> ExecutionPlan | None:
        """Parse an ExecutionPlan from LLM response content.

        Tries JSON extraction first (with markdown code fence stripping),
        then falls back to structured text parsing.
        """
        content = response.content or ""
        if not content.strip():
            logger.warning(
                EXECUTION_PLAN_PARSE_ERROR,
                execution_id=ctx.execution_id,
                reason="empty LLM response content",
            )
            return None

        task_summary = self._extract_task_summary(ctx)

        plan = self._parse_json_plan(
            content,
            task_summary,
            revision_number,
        )
        if plan is not None:
            return plan

        plan = self._parse_text_plan(
            content,
            task_summary,
            revision_number,
        )
        if plan is not None:
            return plan

        logger.warning(
            EXECUTION_PLAN_PARSE_ERROR,
            execution_id=ctx.execution_id,
            content_length=len(content),
            content_snippet=content[:200],
        )
        return None

    def _parse_json_plan(
        self,
        content: str,
        task_summary: str,
        revision_number: int,
    ) -> ExecutionPlan | None:
        """Try to extract a JSON plan from the content."""
        json_str = content.strip()
        fence_match = re.search(
            r"```(?:json)?\s*\n?(.*?)```",
            json_str,
            re.DOTALL,
        )
        if fence_match:
            json_str = fence_match.group(1).strip()

        try:
            data = json.loads(json_str)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.debug(
                EXECUTION_PLAN_PARSE_ERROR,
                parser="json",
                error=str(exc),
            )
            return None

        return self._data_to_plan(data, task_summary, revision_number)

    def _parse_text_plan(
        self,
        content: str,
        task_summary: str,
        revision_number: int,
    ) -> ExecutionPlan | None:
        """Fallback: extract steps from numbered text lines."""
        step_pattern = re.compile(
            r"(?:^|\n)\s*(\d+)\.\s+(.+?)(?=\n\s*\d+\.|\Z)",
            re.DOTALL,
        )
        matches = step_pattern.findall(content)
        if not matches:
            return None

        steps: list[PlanStep] = []
        for _, desc in matches:
            desc_clean = desc.strip()
            if not desc_clean:
                continue
            steps.append(
                PlanStep(
                    step_number=len(steps) + 1,
                    description=desc_clean,
                    expected_outcome=desc_clean,
                )
            )

        if not steps:
            return None

        try:
            return ExecutionPlan(
                steps=tuple(steps),
                revision_number=revision_number,
                original_task_summary=task_summary,
            )
        except ValueError as exc:
            logger.debug(
                EXECUTION_PLAN_PARSE_ERROR,
                parser="text_fallback",
                error=str(exc),
            )
            return None

    def _data_to_plan(
        self,
        data: object,
        task_summary: str,
        revision_number: int,
    ) -> ExecutionPlan | None:
        """Convert parsed JSON data to an ExecutionPlan."""
        if not isinstance(data, dict):
            return None

        raw_steps = data.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            return None

        steps: list[PlanStep] = []
        for i, raw_step in enumerate(raw_steps, start=1):
            if not isinstance(raw_step, dict):
                return None
            desc = raw_step.get("description", "")
            outcome = raw_step.get("expected_outcome", desc)
            if not desc:
                return None
            steps.append(
                PlanStep(
                    step_number=i,
                    description=str(desc),
                    expected_outcome=str(outcome),
                )
            )

        try:
            return ExecutionPlan(
                steps=tuple(steps),
                revision_number=revision_number,
                original_task_summary=task_summary,
            )
        except ValueError as exc:
            logger.debug(
                EXECUTION_PLAN_PARSE_ERROR,
                parser="json_data",
                error=str(exc),
            )
            return None

    # ── Utilities ───────────────────────────────────────────────────

    @staticmethod
    def _extract_task_summary(ctx: AgentContext) -> str:
        """Extract a task summary from the context."""
        if ctx.task_execution is not None:
            return ctx.task_execution.task.title[:200]
        for msg in ctx.conversation:
            if msg.role == MessageRole.USER and msg.content:
                return msg.content[:200]
        return "task"

    @staticmethod
    def _assess_step_success(response: CompletionResponse) -> bool:
        """Determine if a step completed successfully.

        A step is considered successful when the LLM terminates
        normally (STOP or MAX_TOKENS).  MAX_TOKENS is treated as
        success because the step instruction asks the LLM to summarize
        its work; a truncated summary still represents a completed
        step for planning purposes.
        """
        return response.finish_reason in (
            FinishReason.STOP,
            FinishReason.MAX_TOKENS,
        )

    @staticmethod
    def _update_step_status(
        plan: ExecutionPlan,
        step_idx: int,
        status: StepStatus,
    ) -> ExecutionPlan:
        """Return a new plan with the given step's status updated."""
        steps = list(plan.steps)
        steps[step_idx] = steps[step_idx].model_copy(
            update={"status": status},
        )
        return plan.model_copy(update={"steps": tuple(steps)})

    @staticmethod
    def _finalize(
        result: ExecutionResult,
        all_plans: list[ExecutionPlan],
        replans_used: int,
    ) -> ExecutionResult:
        """Attach plan metadata to the execution result."""
        metadata = copy.deepcopy(result.metadata)
        metadata.update(
            {
                "loop_type": "plan_execute",
                "plans": [p.model_dump() for p in all_plans],
                "final_plan": (all_plans[-1].model_dump() if all_plans else None),
                "replans_used": replans_used,
            }
        )
        return result.model_copy(update={"metadata": metadata})
