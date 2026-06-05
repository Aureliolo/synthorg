# module-kind: complex_service
"""Hybrid Plan + ReAct execution loop.

Three-phase approach: plan, execute (mini-ReAct per step with
per-step turn limits), and checkpoint (progress summary + optional
replanning). See ``hybrid.step_helpers`` and ``hybrid.replan_helpers``
for the extracted free-function helpers.

One cohesive responsibility: drive the three-phase Plan + ReAct
execution strategy. The plan, per-step mini-loop with turn limits,
and checkpoint + optional replan phases share the same step-budget
accounting, completion-callback, and execution-context propagation;
the helper modules already extract the free functions, so the
residual orchestrator is the cohesive driver.
"""

import copy
from typing import TYPE_CHECKING

from synthorg.execution.turn import TurnRecord
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_HYBRID_REPLAN_DECIDED,
    EXECUTION_HYBRID_STEP_TURN_LIMIT,
    EXECUTION_LOOP_START,
    EXECUTION_LOOP_TERMINATED,
    EXECUTION_LOOP_TURN_COMPLETE,
    EXECUTION_PLAN_CREATED,
    EXECUTION_PLAN_STEP_COMPLETE,
    EXECUTION_PLAN_STEP_START,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
)

from .hybrid.replan_helpers import attempt_replan, do_replan, run_progress_summary
from .hybrid.step_helpers import (
    build_step_message,
    call_planner,
    handle_step_completion,
    invoke_checkpoint_callback,
    truncate_plan,
    warn_insufficient_budget,
)
from .hybrid_models import HybridLoopConfig
from .intervention.loop_hook import check_steering
from .loop_cancellation import check_task_cancelled
from .loop_control_helpers import (
    check_budget,
    check_shutdown,
    check_stagnation,
    invoke_compaction,
)
from .loop_helpers import (
    build_result,
    call_provider,
    check_response_errors,
    classify_turn,
    get_tool_definitions,
    make_turn_record,
    response_to_message,
)
from .loop_protocol import (
    BudgetChecker,
    ExecutionResult,
    ShutdownChecker,
    TaskCancellationChecker,
    TerminationReason,
)
from .loop_tool_execution import (
    clear_last_turn_tool_calls,
    execute_tool_calls,
)
from .plan_helpers import update_step_status
from .plan_models import (
    ExecutionPlan,
    PlanStep,
    StepStatus,
)
from .plan_parsing import _PLANNING_PROMPT

if TYPE_CHECKING:
    from synthorg.engine.approval_gate import ApprovalGate
    from synthorg.engine.checkpoint.callback import CheckpointCallback
    from synthorg.engine.compaction.protocol import CompactionCallback
    from synthorg.engine.context import AgentContext
    from synthorg.engine.intervention.inbox import SteeringInbox
    from synthorg.engine.stagnation.protocol import StagnationDetector
    from synthorg.providers.models import ToolDefinition
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.tools.protocol import ToolInvokerProtocol

logger = get_logger(__name__)


class HybridLoop:
    """Hybrid Plan + ReAct execution loop.

    Plans, then executes each step as a mini-ReAct loop with a
    per-step turn limit.  Checkpoints after each step with optional
    replanning.

    Args:
        config: Loop configuration (defaults to ``HybridLoopConfig()``).
        checkpoint_callback: Optional per-turn checkpoint callback.
        approval_gate: Optional escalation gate (``None`` disables).
        stagnation_detector: Repetition detector (``None`` disables).
        compaction_callback: Context compaction callback (``None``
            disables).
    """

    def __init__(  # noqa: PLR0913
        self,
        config: HybridLoopConfig | None = None,
        checkpoint_callback: CheckpointCallback | None = None,
        *,
        approval_gate: ApprovalGate | None = None,
        stagnation_detector: StagnationDetector | None = None,
        compaction_callback: CompactionCallback | None = None,
        steering_inbox: SteeringInbox | None = None,
    ) -> None:
        self._config = config or HybridLoopConfig()
        self._checkpoint_callback = checkpoint_callback
        self._approval_gate = approval_gate
        self._stagnation_detector = stagnation_detector
        self._compaction_callback = compaction_callback
        self._steering_inbox = steering_inbox

    @property
    def config(self) -> HybridLoopConfig:
        """Return the loop configuration."""
        return self._config

    @property
    def approval_gate(self) -> ApprovalGate | None:
        """Return the approval gate, or ``None``."""
        return self._approval_gate

    @property
    def stagnation_detector(self) -> StagnationDetector | None:
        """Return the stagnation detector, or ``None``."""
        return self._stagnation_detector

    @property
    def compaction_callback(self) -> CompactionCallback | None:
        """Return the compaction callback, or ``None``."""
        return self._compaction_callback

    @property
    def steering_inbox(self) -> SteeringInbox | None:
        """Return the steering inbox, or ``None``."""
        return self._steering_inbox

    def get_loop_type(self) -> str:
        """Return the loop type identifier."""
        return "hybrid"

    async def execute(  # noqa: PLR0913
        self,
        *,
        context: AgentContext,
        provider: CompletionProvider,
        tool_invoker: ToolInvokerProtocol | None = None,
        budget_checker: BudgetChecker | None = None,
        shutdown_checker: ShutdownChecker | None = None,
        completion_config: CompletionConfig | None = None,
        task_cancellation_checker: TaskCancellationChecker | None = None,
    ) -> ExecutionResult:
        """Run the Hybrid Plan + ReAct loop until termination.

        Args:
            context: Initial agent context with conversation.
            provider: LLM completion provider.
            tool_invoker: Optional tool invoker.
            budget_checker: Optional budget exhaustion callback.
            shutdown_checker: Optional graceful-shutdown callback.
            completion_config: Optional per-execution config override.
            task_cancellation_checker: Optional async callback; returns
                ``True`` when the task was cancelled/superseded externally.

        Returns:
            Execution result with final context and termination info.
        """
        logger.info(
            EXECUTION_LOOP_START,
            execution_id=context.execution_id,
            loop_type=self.get_loop_type(),
            max_turns=context.max_turns,
        )

        ctx = context
        cancel_result = await check_task_cancelled(ctx, task_cancellation_checker, [])
        if cancel_result is not None:
            return self._finalize(cancel_result, [], 0)
        default_model = ctx.identity.model.model_id
        planner_model = self._config.planner_model or default_model
        executor_model = self._config.executor_model or default_model
        default_config = completion_config or CompletionConfig(
            temperature=ctx.identity.model.temperature,
            max_tokens=ctx.identity.model.max_tokens,
        )
        tool_defs = get_tool_definitions(tool_invoker, ctx.loaded_tools)
        turns: list[TurnRecord] = []
        all_plans: list[ExecutionPlan] = []
        replans_used = 0

        warn_insufficient_budget(self._config, ctx)

        # Planning.
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
            return self._finalize(plan_result, all_plans, replans_used)
        ctx, plan = plan_result
        all_plans.append(plan)

        # Execute steps.
        return await self._run_steps(
            ctx,
            provider,
            executor_model,
            planner_model,
            default_config,
            tool_defs,
            tool_invoker,
            plan,
            turns,
            all_plans,
            replans_used,
            budget_checker,
            shutdown_checker,
            task_cancellation_checker,
        )

    # -- Phase orchestration -----------------------------------------------

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
        """Run pre-checks and generate the initial plan.

        Returns:
            ``(updated_ctx, plan)`` when shutdown / budget checks pass
            and the planner succeeds; the terminal :class:`ExecutionResult`
            when any pre-check trips so the caller bails out early.
        """
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
        planner_model: str,
        config: CompletionConfig,
        tool_defs: list[ToolDefinition] | None,
        tool_invoker: ToolInvokerProtocol | None,
        plan: ExecutionPlan,
        turns: list[TurnRecord],
        all_plans: list[ExecutionPlan],
        replans_used: int,
        budget_checker: BudgetChecker | None,
        shutdown_checker: ShutdownChecker | None,
        task_cancellation_checker: TaskCancellationChecker | None = None,
    ) -> ExecutionResult:
        """Iterate through plan steps with checkpointing/replanning.

        Returns:
            The terminal :class:`ExecutionResult` once the loop exits
            (success, budget exhausted, shutdown, cancellation, or replan
            exhaustion).
        """
        step_idx = 0
        while step_idx < len(plan.steps):
            if not ctx.has_turns_remaining:
                break

            step = plan.steps[step_idx]
            plan = update_step_status(
                plan,
                step_idx,
                StepStatus.IN_PROGRESS,
            )
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
                task_cancellation_checker,
            )

            if isinstance(step_result, ExecutionResult):
                return self._finalize(
                    step_result,
                    all_plans,
                    replans_used,
                )

            ctx, step_ok = step_result

            if step_ok:
                outcome = await self._handle_completed_step(
                    ctx,
                    provider,
                    planner_model,
                    config,
                    plan,
                    step,
                    step_idx,
                    turns,
                    all_plans,
                    replans_used,
                    budget_checker,
                    shutdown_checker,
                )
                if isinstance(outcome, ExecutionResult):
                    return outcome
                ctx, plan, replans_used, restart = outcome
                # A REDIRECT adopted mid-step forces a replan at this safe
                # boundary so the revised plan honours the directive.
                if not restart and ctx.pending_steering_replan_id is not None:
                    steer_out = await self._steering_replan_hybrid(
                        ctx,
                        provider,
                        planner_model,
                        config,
                        plan,
                        step,
                        turns,
                        all_plans,
                        replans_used,
                    )
                    if isinstance(steer_out, ExecutionResult):
                        return steer_out
                    ctx, plan, replans_used = steer_out
                    restart = True
                elif restart and ctx.pending_steering_replan_id is not None:
                    # A completion-triggered replan already re-planned with the
                    # adopted directive in conversation context, so a dedicated
                    # steering replan would be redundant. Clear the pending flag
                    # so it does not linger to fire a stale replan on a later
                    # step or persist into a terminal checkpoint.
                    ctx = ctx.cleared_pending_replan()
                if restart:
                    step_idx = 0
                    continue
                step_idx += 1
                continue

            # Step failed -- attempt re-planning
            replan_out = await attempt_replan(
                self._config,
                ctx,
                provider,
                planner_model,
                config,
                plan,
                step,
                step_idx,
                turns,
                all_plans,
                replans_used,
                budget_checker,
                shutdown_checker,
                finalize=self._finalize,
                checkpoint_callback=self._checkpoint_callback,
            )
            if isinstance(replan_out, ExecutionResult):
                return replan_out
            ctx, plan, replans_used = replan_out
            step_idx = 0

        return self._build_final_result(
            ctx,
            plan,
            step_idx,
            turns,
            all_plans,
            replans_used,
        )

    async def _handle_completed_step(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        planner_model: str,
        config: CompletionConfig,
        plan: ExecutionPlan,
        step: PlanStep,
        step_idx: int,
        turns: list[TurnRecord],
        all_plans: list[ExecutionPlan],
        replans_used: int,
        budget_checker: BudgetChecker | None,
        shutdown_checker: ShutdownChecker | None,
    ) -> tuple[AgentContext, ExecutionPlan, int, bool] | ExecutionResult:
        """Handle a completed step: update status, checkpoint, replan.

        Returns:
            Either a terminal :class:`ExecutionResult` when the
            progress summary halts the loop, or
            ``(ctx, plan, replans_used, restart)`` where ``restart``
            asks the outer loop to begin from step 0 after a replan.
        """
        plan = update_step_status(
            plan,
            step_idx,
            StepStatus.COMPLETED,
        )
        if all_plans:
            all_plans[-1] = plan
        logger.info(
            EXECUTION_PLAN_STEP_COMPLETE,
            execution_id=ctx.execution_id,
            step_number=step.step_number,
        )

        if not self._config.checkpoint_after_each_step:
            return ctx, plan, replans_used, False

        summary_result = await run_progress_summary(
            self._config,
            self._checkpoint_callback,
            ctx,
            provider,
            planner_model,
            config,
            plan,
            step_idx,
            turns,
            budget_checker,
            shutdown_checker,
        )
        if isinstance(summary_result, ExecutionResult):
            return self._finalize(
                summary_result,
                all_plans,
                replans_used,
            )
        ctx, should_replan = summary_result

        return await self._decide_replan_on_completion(
            ctx,
            provider,
            planner_model,
            config,
            plan,
            step,
            step_idx,
            turns,
            all_plans,
            replans_used,
            budget_checker,
            shutdown_checker,
            should_replan=should_replan,
        )

    async def _decide_replan_on_completion(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        planner_model: str,
        config: CompletionConfig,
        plan: ExecutionPlan,
        step: PlanStep,
        step_idx: int,
        turns: list[TurnRecord],
        all_plans: list[ExecutionPlan],
        replans_used: int,
        budget_checker: BudgetChecker | None,
        shutdown_checker: ShutdownChecker | None,
        *,
        should_replan: bool,
    ) -> tuple[AgentContext, ExecutionPlan, int, bool] | ExecutionResult:
        """Decide whether to replan after a successful step.

        Returns:
            ``(ctx, plan, replans_used, should_restart)`` or
            ``ExecutionResult`` for termination conditions.
        """
        if not (
            should_replan
            and self._config.allow_replan_on_completion
            and replans_used < self._config.max_replans
            and step_idx < len(plan.steps) - 1
            and ctx.has_turns_remaining
        ):
            return ctx, plan, replans_used, False

        shutdown_result = check_shutdown(ctx, shutdown_checker, turns)
        if shutdown_result is not None:
            return self._finalize(shutdown_result, all_plans, replans_used)
        budget_result = check_budget(ctx, budget_checker, turns)
        if budget_result is not None:
            return self._finalize(budget_result, all_plans, replans_used)

        replan_result = await do_replan(
            self._config,
            ctx,
            provider,
            planner_model,
            config,
            plan,
            step,
            turns,
            step_failed=False,
            checkpoint_callback=self._checkpoint_callback,
        )
        if isinstance(replan_result, ExecutionResult):
            return self._finalize(
                replan_result,
                all_plans,
                replans_used,
            )
        ctx, plan = replan_result
        replans_used += 1
        all_plans.append(plan)
        logger.info(
            EXECUTION_HYBRID_REPLAN_DECIDED,
            execution_id=ctx.execution_id,
            trigger="completion_summary",
            replans_used=replans_used,
        )
        return ctx, plan, replans_used, True

    async def _steering_replan_hybrid(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        planner_model: str,
        config: CompletionConfig,
        plan: ExecutionPlan,
        step: PlanStep,
        turns: list[TurnRecord],
        all_plans: list[ExecutionPlan],
        replans_used: int,
    ) -> tuple[AgentContext, ExecutionPlan, int] | ExecutionResult:
        """Replan after adopting a mid-flight steering REDIRECT.

        The directive is already in ``ctx`` (injected at the turn boundary);
        this revises the remaining plan to honour it. Operator-driven and
        consume-once, so it does not count against ``max_replans``; the
        pending-replan id is cleared here.

        Returns:
            ``(ctx, new_plan, replans_used)`` on success, or a terminal
            :class:`ExecutionResult`.
        """
        result = await do_replan(
            self._config,
            ctx,
            provider,
            planner_model,
            config,
            plan,
            step,
            turns,
            step_failed=False,
            checkpoint_callback=self._checkpoint_callback,
        )
        if isinstance(result, ExecutionResult):
            return self._finalize(result, all_plans, replans_used)
        ctx, new_plan = result
        ctx = ctx.cleared_pending_replan()
        all_plans.append(new_plan)
        logger.info(
            EXECUTION_HYBRID_REPLAN_DECIDED,
            execution_id=ctx.execution_id,
            trigger="steering",
            replans_used=replans_used,
        )
        return ctx, new_plan, replans_used

    def _build_final_result(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        plan: ExecutionPlan,
        step_idx: int,
        turns: list[TurnRecord],
        all_plans: list[ExecutionPlan],
        replans_used: int,
    ) -> ExecutionResult:
        """Build the final result after step iteration completes.

        Returns:
            The terminal :class:`ExecutionResult`, with a
            ``MAX_TURNS`` termination reason when turns ran out
            mid-plan and ``COMPLETED`` otherwise.
        """
        # Sync live plan into all_plans so final_plan reflects
        # step status changes (COMPLETED, IN_PROGRESS, etc.).
        if all_plans:
            all_plans[-1] = plan

        if not ctx.has_turns_remaining and step_idx < len(plan.steps):
            logger.info(
                EXECUTION_LOOP_TERMINATED,
                execution_id=ctx.execution_id,
                reason=TerminationReason.MAX_TURNS.value,
                turns=len(turns),
            )
            return self._finalize(
                build_result(
                    ctx,
                    TerminationReason.MAX_TURNS,
                    turns,
                ),
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

    # -- Planning ----------------------------------------------------------

    async def _generate_plan(
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        planner_model: str,
        config: CompletionConfig,
        turns: list[TurnRecord],
    ) -> tuple[AgentContext, ExecutionPlan] | ExecutionResult:
        """Generate an execution plan from the LLM.

        Returns:
            ``(updated_ctx, plan)`` on a successful plan generation,
            or the terminal :class:`ExecutionResult` propagated from
            :func:`call_planner` (budget exhaustion, shutdown, etc.).
        """
        plan_msg = ChatMessage(
            role=MessageRole.USER,
            content=_PLANNING_PROMPT,
        )
        result = await call_planner(
            ctx,
            provider,
            planner_model,
            config,
            turns,
            plan_msg,
            checkpoint_callback=self._checkpoint_callback,
        )
        if isinstance(result, ExecutionResult):
            return result
        ctx, plan = result
        plan = truncate_plan(
            plan,
            self._config.max_plan_steps,
            ctx.execution_id,
        )
        logger.info(
            EXECUTION_PLAN_CREATED,
            execution_id=ctx.execution_id,
            step_count=len(plan.steps),
            revision=plan.revision_number,
        )
        return ctx, plan

    # -- Step execution ----------------------------------------------------

    async def _execute_step(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        executor_model: str,
        config: CompletionConfig,
        tool_defs: list[ToolDefinition] | None,
        tool_invoker: ToolInvokerProtocol | None,
        step: PlanStep,
        turns: list[TurnRecord],
        budget_checker: BudgetChecker | None,
        shutdown_checker: ShutdownChecker | None,
        task_cancellation_checker: TaskCancellationChecker | None = None,
    ) -> tuple[AgentContext, bool] | ExecutionResult:
        """Execute a single plan step via a mini-ReAct sub-loop.

        Returns:
            ``(ctx, True)`` on success, ``(ctx, False)`` on step
            failure, or ``ExecutionResult`` for termination.
        """
        ctx = ctx.with_message(build_step_message(step))
        step_start_idx = len(turns)
        step_corrections = 0
        step_turns = 0
        max_step_turns = self._config.max_turns_per_step

        while ctx.has_turns_remaining and step_turns < max_step_turns:
            # Refresh tool defs so newly loaded tools appear
            tool_defs = get_tool_definitions(tool_invoker, ctx.loaded_tools)
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
                task_cancellation_checker,
            )
            step_turns += 1

            if isinstance(result, ExecutionResult):
                return result
            if isinstance(result, tuple):
                ctx, step_ok = result
                ctx = await self._compact(ctx)
                return ctx, step_ok
            ctx = result

            ctx = await self._compact(ctx)

            # Per-step stagnation detection (step-scoped turns)
            stag_outcome = await check_stagnation(
                ctx,
                self._stagnation_detector,
                turns[step_start_idx:],
                step_corrections,
                execution_id=ctx.execution_id,
                step_number=step.step_number,
            )
            if isinstance(stag_outcome, ExecutionResult):
                return stag_outcome.model_copy(
                    update={"turns": tuple(turns)},
                )
            if isinstance(stag_outcome, tuple):
                ctx, step_corrections = stag_outcome

        # Loop exited without step completion
        if not ctx.has_turns_remaining:
            return ctx, False
        logger.warning(
            EXECUTION_HYBRID_STEP_TURN_LIMIT,
            execution_id=ctx.execution_id,
            step_number=step.step_number,
            max_turns_per_step=self._config.max_turns_per_step,
        )
        return ctx, False

    async def _compact(self, ctx: AgentContext) -> AgentContext:
        """Run context compaction at turn boundaries.

        Returns:
            The compacted context returned by the compaction callback,
            or ``ctx`` unchanged when compaction is disabled or
            returns ``None``.
        """
        compacted = await invoke_compaction(
            ctx,
            self._compaction_callback,
            ctx.turn_count,
        )
        return compacted if compacted is not None else ctx

    async def _run_step_turn(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        model: str,
        config: CompletionConfig,
        tool_defs: list[ToolDefinition] | None,
        tool_invoker: ToolInvokerProtocol | None,
        turns: list[TurnRecord],
        budget_checker: BudgetChecker | None,
        shutdown_checker: ShutdownChecker | None,
        task_cancellation_checker: TaskCancellationChecker | None = None,
    ) -> AgentContext | ExecutionResult | tuple[AgentContext, bool]:
        """Execute a single turn within a step's mini-ReAct sub-loop.

        Returns:
            ``AgentContext`` to continue the loop, ``(ctx, bool)``
            for step completion, or ``ExecutionResult`` for
            termination.
        """
        shutdown_result = check_shutdown(ctx, shutdown_checker, turns)
        if shutdown_result is not None:
            return shutdown_result
        budget_result = check_budget(ctx, budget_checker, turns)
        if budget_result is not None:
            return budget_result
        cancel_result = await check_task_cancelled(
            ctx, task_cancellation_checker, turns
        )
        if cancel_result is not None:
            return cancel_result
        # Adopt pending steering directives before the LLM call so the
        # operator's constraint is in context for this step's turn.
        steered = await check_steering(
            ctx, self._steering_inbox, execution_id=ctx.execution_id
        )
        if steered is not None:
            ctx = steered

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

        turns.append(
            make_turn_record(
                turn_number,
                response,
                call_category=classify_turn(turn_number, response, ctx),
                provider_metadata=response.provider_metadata,
            )
        )

        error = check_response_errors(
            ctx,
            response,
            turn_number,
            turns,
        )
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

        await invoke_checkpoint_callback(
            self._checkpoint_callback,
            ctx,
            turn_number,
        )

        if not response.tool_calls:
            return handle_step_completion(ctx, response, turn_number)

        return await self._handle_step_tool_calls(
            ctx,
            tool_invoker,
            response,
            turn_number,
            turns,
            shutdown_checker,
        )

    async def _handle_step_tool_calls(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        tool_invoker: ToolInvokerProtocol | None,
        response: CompletionResponse,
        turn_number: int,
        turns: list[TurnRecord],
        shutdown_checker: ShutdownChecker | None,
    ) -> AgentContext | ExecutionResult:
        """Check shutdown and execute tool calls for a step turn.

        Returns:
            The :class:`AgentContext` propagated from tool execution
            (with tool outputs appended) or a terminal
            :class:`ExecutionResult` when a shutdown intervenes.
        """
        shutdown_result = check_shutdown(ctx, shutdown_checker, turns)
        if shutdown_result is not None:
            clear_last_turn_tool_calls(turns)
            return shutdown_result.model_copy(
                update={"turns": tuple(turns)},
            )

        return await execute_tool_calls(
            ctx,
            tool_invoker,
            response,
            turn_number,
            turns,
            approval_gate=self._approval_gate,
        )

    # -- Utilities ---------------------------------------------------------

    @staticmethod
    def _finalize(
        result: ExecutionResult,
        all_plans: list[ExecutionPlan],
        replans_used: int,
    ) -> ExecutionResult:
        """Attach hybrid metadata to the execution result.

        Returns:
            A copy of ``result`` whose metadata carries the hybrid
            loop's plan history, final plan dump, and replan count.
        """
        metadata = copy.deepcopy(result.metadata)
        metadata.update(
            {
                "loop_type": "hybrid",
                "plans": [p.model_dump() for p in all_plans],
                "final_plan": (all_plans[-1].model_dump() if all_plans else None),
                "replans_used": replans_used,
            }
        )
        return result.model_copy(update={"metadata": metadata})
