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

from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.checkpoint.callback import CheckpointCallback
from synthorg.engine.compaction.protocol import CompactionCallback
from synthorg.engine.context import AgentContext
from synthorg.engine.intervention.inbox import SteeringInbox
from synthorg.engine.quality.classifier import StepQualityClassifier
from synthorg.engine.quality.models import StepQualitySignal
from synthorg.engine.stagnation.protocol import StagnationDetector
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
    ToolDefinition,
)
from synthorg.providers.protocol import CompletionProvider
from synthorg.tools.protocol import ToolInvokerProtocol

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
    check_response_errors,
    classify_step,
    classify_turn,
    get_tool_definitions,
    make_turn_record,
    notify_turn_observer,
    response_to_message,
)
from .loop_protocol import (
    BudgetChecker,
    ExecutionResult,
    ShutdownChecker,
    TaskCancellationChecker,
    TerminationReason,
    TurnObserver,
)
from .loop_streaming import (
    _TurnInterrupted,
    fold_interrupt_usage,
    run_provider_turn,
)
from .loop_tool_execution import (
    clear_last_turn_tool_calls,
    execute_tool_calls,
)
from .plan_helpers import update_step_status
from .plan_loop_context import StepRunContext, StepRunState, StepTurnOutcome
from .plan_models import (
    ExecutionPlan,
    PlanStep,
    StepStatus,
)
from .plan_parsing import _PLANNING_PROMPT

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
        step_classifier: Optional step-quality classifier scored once per
            mini-ReAct step from that step's turns; ``None`` disables
            quality classification.
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
        step_classifier: StepQualityClassifier | None = None,
    ) -> None:
        self._config = config or HybridLoopConfig()
        self._checkpoint_callback = checkpoint_callback
        self._approval_gate = approval_gate
        self._stagnation_detector = stagnation_detector
        self._compaction_callback = compaction_callback
        self._steering_inbox = steering_inbox
        self._step_classifier = step_classifier

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
        turn_observer: TurnObserver | None = None,
        streaming_enabled: bool = False,
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
            turn_observer: Optional per-run progress callback; fired once
                per plan step so the AG-UI stream surfaces step-level
                progress for this loop.
            streaming_enabled: When ``True``, each step-execution LLM call
                streams and is interruptible mid-flight (operator
                cancellation and steering REDIRECT).

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
        run = StepRunContext(
            provider=provider,
            executor_model=self._config.executor_model or default_model,
            planner_model=self._config.planner_model or default_model,
            completion_config=completion_config
            or CompletionConfig(
                temperature=ctx.identity.model.temperature,
                max_tokens=ctx.identity.model.max_tokens,
            ),
            tool_invoker=tool_invoker,
            budget_checker=budget_checker,
            shutdown_checker=shutdown_checker,
            task_cancellation_checker=task_cancellation_checker,
            turn_observer=turn_observer,
            checkpoint_callback=self._checkpoint_callback,
            streaming_enabled=streaming_enabled,
        )
        turns: list[TurnRecord] = []
        all_plans: list[ExecutionPlan] = []

        warn_insufficient_budget(self._config, ctx)

        # Planning.
        plan_result = await self._run_planning_phase(run, ctx, turns)
        if isinstance(plan_result, ExecutionResult):
            return self._finalize(plan_result, all_plans, 0)
        ctx, plan = plan_result
        all_plans.append(plan)

        # Execute steps.
        return await self._run_steps(
            run,
            StepRunState(ctx=ctx, plan=plan, turns=turns, all_plans=all_plans),
        )

    # -- Phase orchestration -----------------------------------------------

    async def _run_planning_phase(
        self,
        run: StepRunContext,
        ctx: AgentContext,
        turns: list[TurnRecord],
    ) -> tuple[AgentContext, ExecutionPlan] | ExecutionResult:
        """Run pre-checks and generate the initial plan.

        Returns:
            ``(updated_ctx, plan)`` when shutdown / budget checks pass
            and the planner succeeds; the terminal :class:`ExecutionResult`
            when any pre-check trips so the caller bails out early.
        """
        shutdown_result = check_shutdown(ctx, run.shutdown_checker, turns)
        if shutdown_result is not None:
            return shutdown_result
        budget_result = check_budget(ctx, run.budget_checker, turns)
        if budget_result is not None:
            return budget_result
        return await self._generate_plan(run, ctx, turns)

    async def _run_steps(
        self,
        run: StepRunContext,
        state: StepRunState,
    ) -> ExecutionResult:
        """Iterate through plan steps with checkpointing/replanning.

        Returns:
            The terminal :class:`ExecutionResult` once the loop exits
            (success, budget exhausted, shutdown, cancellation, or replan
            exhaustion).
        """
        signals: list[StepQualitySignal] = []
        while state.step_idx < len(state.plan.steps):
            if not state.ctx.has_turns_remaining:
                break

            step = state.plan.steps[state.step_idx]
            state.plan = update_step_status(
                state.plan,
                state.step_idx,
                StepStatus.IN_PROGRESS,
            )
            logger.info(
                EXECUTION_PLAN_STEP_START,
                execution_id=state.ctx.execution_id,
                step_number=step.step_number,
                description=step.description,
            )
            await notify_turn_observer(
                run.turn_observer, step.step_number, (step.description,)
            )

            step_start = len(state.turns)
            step_result = await self._execute_step(run, state, step)

            if isinstance(step_result, ExecutionResult):
                # The in-flight step ends here (cancel / shutdown / budget /
                # stagnation / error). Classify it too so its signal is not
                # dropped from quality_signals, which the worker health
                # pipeline consumes downstream.
                step_turns = tuple(state.turns[step_start:])
                if step_turns:
                    step_signal = await classify_step(
                        self._step_classifier,
                        step_index=state.step_idx,
                        step_turns=step_turns,
                        termination_reason=step_result.termination_reason,
                    )
                    if step_signal is not None:
                        signals.append(step_signal)
                return self._attach_signals(
                    self._finalize(
                        step_result,
                        state.all_plans,
                        state.replans_used,
                    ),
                    signals,
                )

            step_ok = step_result
            step_signal = await classify_step(
                self._step_classifier,
                step_index=state.step_idx,
                step_turns=tuple(state.turns[step_start:]),
                termination_reason=(
                    TerminationReason.COMPLETED
                    if step_ok
                    else TerminationReason.MAX_TURNS
                ),
            )
            if step_signal is not None:
                signals.append(step_signal)

            if step_ok:
                outcome = await self._handle_completed_step(run, state, step)
                if isinstance(outcome, ExecutionResult):
                    return self._attach_signals(outcome, signals)
                restart = outcome
                # A REDIRECT adopted mid-step forces a replan at this safe
                # boundary so the revised plan honours the directive.
                if not restart and state.ctx.pending_steering_replan_id is not None:
                    steer_out = await self._steering_replan_hybrid(run, state, step)
                    if steer_out is not None:
                        return self._attach_signals(steer_out, signals)
                    restart = True
                elif restart and state.ctx.pending_steering_replan_id is not None:
                    # A completion-triggered replan already re-planned with the
                    # adopted directive in conversation context, so a dedicated
                    # steering replan would be redundant. Clear the pending flag
                    # so it does not linger to fire a stale replan on a later
                    # step or persist into a terminal checkpoint.
                    state.ctx = state.ctx.cleared_pending_replan()
                state.step_idx = 0 if restart else state.step_idx + 1
                continue

            # Step failed -- attempt re-planning
            replan_out = await attempt_replan(
                self._config,
                run,
                state,
                step,
                finalize=self._finalize,
            )
            if replan_out is not None:
                return self._attach_signals(replan_out, signals)
            state.step_idx = 0

        return self._attach_signals(self._build_final_result(state), signals)

    @staticmethod
    def _attach_signals(
        result: ExecutionResult,
        signals: list[StepQualitySignal],
    ) -> ExecutionResult:
        """Attach accumulated per-step quality signals to a terminal result.

        Returns:
            ``result`` unchanged when no signals were produced, else a
            copy carrying the per-step ``quality_signals`` tuple.
        """
        if not signals:
            return result
        return result.model_copy(update={"quality_signals": tuple(signals)})

    async def _handle_completed_step(
        self,
        run: StepRunContext,
        state: StepRunState,
        step: PlanStep,
    ) -> bool | ExecutionResult:
        """Handle a completed step: update status, checkpoint, replan.

        Returns:
            Either a terminal :class:`ExecutionResult` when the
            progress summary halts the loop, or a ``restart`` flag asking
            the outer loop to begin again from step 0 after a replan.
        """
        state.plan = update_step_status(
            state.plan,
            state.step_idx,
            StepStatus.COMPLETED,
        )
        if state.all_plans:
            state.all_plans[-1] = state.plan
        logger.info(
            EXECUTION_PLAN_STEP_COMPLETE,
            execution_id=state.ctx.execution_id,
            step_number=step.step_number,
        )

        if not self._config.checkpoint_after_each_step:
            return False

        summary_result = await run_progress_summary(self._config, run, state)
        if isinstance(summary_result, ExecutionResult):
            return self._finalize(
                summary_result,
                state.all_plans,
                state.replans_used,
            )

        return await self._decide_replan_on_completion(
            run,
            state,
            step,
            should_replan=summary_result,
        )

    async def _decide_replan_on_completion(
        self,
        run: StepRunContext,
        state: StepRunState,
        step: PlanStep,
        *,
        should_replan: bool,
    ) -> bool | ExecutionResult:
        """Decide whether to replan after a successful step.

        Returns:
            The ``should_restart`` flag, or an :class:`ExecutionResult`
            for termination conditions.
        """
        if not (
            should_replan
            and self._config.allow_replan_on_completion
            and state.replans_used < self._config.max_replans
            and state.step_idx < len(state.plan.steps) - 1
            and state.ctx.has_turns_remaining
        ):
            return False

        shutdown_result = check_shutdown(state.ctx, run.shutdown_checker, state.turns)
        if shutdown_result is not None:
            return self._finalize(shutdown_result, state.all_plans, state.replans_used)
        budget_result = check_budget(state.ctx, run.budget_checker, state.turns)
        if budget_result is not None:
            return self._finalize(budget_result, state.all_plans, state.replans_used)

        replan_result = await do_replan(
            self._config,
            run,
            state,
            step,
            step_failed=False,
        )
        if isinstance(replan_result, ExecutionResult):
            return self._finalize(
                replan_result,
                state.all_plans,
                state.replans_used,
            )
        state.plan = replan_result
        state.replans_used += 1
        state.all_plans.append(state.plan)
        logger.info(
            EXECUTION_HYBRID_REPLAN_DECIDED,
            execution_id=state.ctx.execution_id,
            trigger="completion_summary",
            replans_used=state.replans_used,
        )
        return True

    async def _steering_replan_hybrid(
        self,
        run: StepRunContext,
        state: StepRunState,
        step: PlanStep,
    ) -> ExecutionResult | None:
        """Replan after adopting a mid-flight steering REDIRECT.

        The directive is already in ``state.ctx`` (injected at the turn
        boundary); this revises the remaining plan to honour it.
        Operator-driven and consume-once, so it does not count against
        ``max_replans``; the pending-replan id is cleared here.

        Returns:
            ``None`` once the revised plan is adopted, or a terminal
            :class:`ExecutionResult`.
        """
        result = await do_replan(
            self._config,
            run,
            state,
            step,
            step_failed=False,
        )
        if isinstance(result, ExecutionResult):
            return self._finalize(result, state.all_plans, state.replans_used)
        state.plan = result
        state.ctx = state.ctx.cleared_pending_replan()
        state.all_plans.append(state.plan)
        logger.info(
            EXECUTION_HYBRID_REPLAN_DECIDED,
            execution_id=state.ctx.execution_id,
            trigger="steering",
            replans_used=state.replans_used,
        )
        return None

    def _build_final_result(self, state: StepRunState) -> ExecutionResult:
        """Build the final result after step iteration completes.

        Returns:
            The terminal :class:`ExecutionResult`, with a
            ``MAX_TURNS`` termination reason when turns ran out
            mid-plan and ``COMPLETED`` otherwise.
        """
        # Sync live plan into all_plans so final_plan reflects
        # step status changes (COMPLETED, IN_PROGRESS, etc.).
        if state.all_plans:
            state.all_plans[-1] = state.plan

        if not state.ctx.has_turns_remaining and state.step_idx < len(state.plan.steps):
            logger.info(
                EXECUTION_LOOP_TERMINATED,
                execution_id=state.ctx.execution_id,
                reason=TerminationReason.MAX_TURNS.value,
                turns=len(state.turns),
            )
            return self._finalize(
                build_result(
                    state.ctx,
                    TerminationReason.MAX_TURNS,
                    state.turns,
                ),
                state.all_plans,
                state.replans_used,
            )

        logger.info(
            EXECUTION_LOOP_TERMINATED,
            execution_id=state.ctx.execution_id,
            reason=TerminationReason.COMPLETED.value,
            turns=len(state.turns),
        )
        return self._finalize(
            build_result(state.ctx, TerminationReason.COMPLETED, state.turns),
            state.all_plans,
            state.replans_used,
        )

    # -- Planning ----------------------------------------------------------

    async def _generate_plan(
        self,
        run: StepRunContext,
        ctx: AgentContext,
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
        result = await call_planner(run, ctx, turns, plan_msg)
        if isinstance(result, ExecutionResult):
            return result
        ctx, plan = result
        plan = truncate_plan(
            plan,
            self._config.max_plan_steps,
        )
        logger.info(
            EXECUTION_PLAN_CREATED,
            execution_id=ctx.execution_id,
            step_count=len(plan.steps),
            revision=plan.revision_number,
        )
        return ctx, plan

    # -- Step execution ----------------------------------------------------

    async def _execute_step(
        self,
        run: StepRunContext,
        state: StepRunState,
        step: PlanStep,
    ) -> bool | ExecutionResult:
        """Execute a single plan step via a mini-ReAct sub-loop.

        Returns:
            ``True`` on success, ``False`` on step failure, or
            ``ExecutionResult`` for termination.
        """
        state.ctx = state.ctx.with_message(build_step_message(step))
        step_start_idx = len(state.turns)
        step_corrections = 0
        # Count COMPLETED turns via ``ctx.turn_count`` delta rather than an
        # independent per-call counter: a mid-turn steering REDIRECT re-issues
        # the turn (``_TurnInterrupted``) without advancing ``ctx.turn_count``,
        # so it must not consume the per-step turn budget.
        step_start_turn_count = state.ctx.turn_count
        max_step_turns = self._config.max_turns_per_step

        while (
            state.ctx.has_turns_remaining
            and state.ctx.turn_count - step_start_turn_count < max_step_turns
        ):
            # Refresh tool defs so newly loaded tools appear
            tool_defs = get_tool_definitions(run.tool_invoker, state.ctx.loaded_tools)
            result = await self._run_step_turn(run, state, tool_defs)

            if isinstance(result, ExecutionResult):
                return result
            state.ctx = await self._compact(state.ctx)
            if isinstance(result, bool):
                return result

            # Per-step stagnation detection (step-scoped turns)
            stag_outcome = await check_stagnation(
                state.ctx,
                self._stagnation_detector,
                state.turns[step_start_idx:],
                step_corrections,
                step_number=step.step_number,
            )
            if isinstance(stag_outcome, ExecutionResult):
                return stag_outcome.model_copy(
                    update={"turns": tuple(state.turns)},
                )
            if isinstance(stag_outcome, tuple):
                state.ctx, step_corrections = stag_outcome

        # Loop exited without step completion
        if not state.ctx.has_turns_remaining:
            return False
        logger.warning(
            EXECUTION_HYBRID_STEP_TURN_LIMIT,
            execution_id=state.ctx.execution_id,
            step_number=step.step_number,
            max_turns_per_step=self._config.max_turns_per_step,
        )
        return False

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

    async def _run_step_turn(
        self,
        run: StepRunContext,
        state: StepRunState,
        tool_defs: list[ToolDefinition] | None,
    ) -> StepTurnOutcome | bool | ExecutionResult:
        """Execute a single turn within a step's mini-ReAct sub-loop.

        Returns:
            :attr:`StepTurnOutcome.CONTINUE` to keep the sub-loop running
            (also the re-issue path after a mid-turn steering REDIRECT), a
            ``bool`` carrying the step's success once it completes, or an
            ``ExecutionResult`` for termination.
        """
        shutdown_result = check_shutdown(state.ctx, run.shutdown_checker, state.turns)
        if shutdown_result is not None:
            return shutdown_result
        budget_result = check_budget(state.ctx, run.budget_checker, state.turns)
        if budget_result is not None:
            return budget_result
        cancel_result = await check_task_cancelled(
            state.ctx, run.task_cancellation_checker, state.turns
        )
        if cancel_result is not None:
            return cancel_result
        # Adopt pending steering directives before the LLM call so the
        # operator's constraint is in context for this step's turn.
        steered = await check_steering(state.ctx, self._steering_inbox)
        if steered is not None:
            state.ctx = steered

        turn_number = state.ctx.turn_count + 1
        outcome = await run_provider_turn(
            state.ctx,
            run.provider,
            run.executor_model,
            tool_defs=tool_defs,
            config=run.completion_config,
            turn_number=turn_number,
            turns=state.turns,
            streaming_enabled=run.streaming_enabled,
            cancellation_checker=run.task_cancellation_checker,
            steering_inbox=self._steering_inbox,
        )
        if isinstance(outcome, ExecutionResult):
            return outcome
        if isinstance(outcome, _TurnInterrupted):
            # Re-issue the step turn: continuing the mini-sub-loop lets its
            # top-of-turn steering check adopt the REDIRECT the interrupt
            # fired for.
            state.ctx = fold_interrupt_usage(state.ctx, outcome)
            return StepTurnOutcome.CONTINUE
        response = outcome

        state.turns.append(
            make_turn_record(
                turn_number,
                response,
                call_category=classify_turn(turn_number, response, state.ctx),
                provider_metadata=response.provider_metadata,
            )
        )

        error = check_response_errors(
            state.ctx,
            response,
            turn_number,
            state.turns,
        )
        if error is not None:
            return error

        state.ctx = state.ctx.with_turn_completed(
            response.usage,
            response_to_message(response),
        )
        logger.info(
            EXECUTION_LOOP_TURN_COMPLETE,
            execution_id=state.ctx.execution_id,
            turn=turn_number,
            finish_reason=response.finish_reason.value,
            tool_call_count=len(response.tool_calls),
        )

        await invoke_checkpoint_callback(
            run.checkpoint_callback,
            state.ctx,
            turn_number,
        )

        if not response.tool_calls:
            state.ctx, step_ok = handle_step_completion(
                state.ctx, response, turn_number
            )
            return step_ok

        return await self._handle_step_tool_calls(
            run,
            state,
            response,
            turn_number,
        )

    async def _handle_step_tool_calls(
        self,
        run: StepRunContext,
        state: StepRunState,
        response: CompletionResponse,
        turn_number: int,
    ) -> StepTurnOutcome | ExecutionResult:
        """Check shutdown and execute tool calls for a step turn.

        Returns:
            :attr:`StepTurnOutcome.CONTINUE` once the tool outputs are
            appended to ``state.ctx``, or a terminal
            :class:`ExecutionResult` when a shutdown intervenes.
        """
        shutdown_result = check_shutdown(state.ctx, run.shutdown_checker, state.turns)
        if shutdown_result is not None:
            clear_last_turn_tool_calls(state.turns)
            return shutdown_result.model_copy(
                update={"turns": tuple(state.turns)},
            )

        result = await execute_tool_calls(
            state.ctx,
            run.tool_invoker,
            response,
            turn_number,
            state.turns,
            approval_gate=self._approval_gate,
        )
        if isinstance(result, ExecutionResult):
            return result
        state.ctx = result
        return StepTurnOutcome.CONTINUE

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
