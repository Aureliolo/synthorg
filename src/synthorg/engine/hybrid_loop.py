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
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_HYBRID_REPLAN_DECIDED,
    EXECUTION_HYBRID_STEP_TURN_LIMIT,
    EXECUTION_LOOP_ERROR,
    EXECUTION_LOOP_START,
    EXECUTION_LOOP_TERMINATED,
    EXECUTION_PLAN_CREATED,
    EXECUTION_PLAN_STEP_COMPLETE,
    EXECUTION_PLAN_STEP_START,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
)
from synthorg.providers.protocol import CompletionProvider
from synthorg.tools.protocol import ToolInvokerProtocol

from .hybrid.replan_helpers import attempt_replan, do_replan, run_progress_summary
from .hybrid.step_helpers import (
    build_step_message,
    truncate_plan,
    warn_insufficient_budget,
)
from .hybrid_models import HybridLoopConfig
from .loop_cancellation import check_task_cancelled
from .loop_control_helpers import (
    check_budget,
    check_shutdown,
    check_stagnation,
    invoke_compaction,
)
from .loop_helpers import (
    build_result,
    classify_step,
    get_tool_definitions,
    notify_turn_observer,
)
from .loop_protocol import (
    BudgetChecker,
    ExecutionResult,
    ShutdownChecker,
    TaskCancellationChecker,
    TerminationReason,
    TurnObserver,
)
from .plan_helpers import (
    call_planner,
    clear_superseded_directive,
    update_step_status,
)
from .plan_loop_context import (
    ReplanTrigger,
    ReplanVerdict,
    StepRunContext,
    StepRunState,
    StepTurnOutcome,
)
from .plan_models import (
    ExecutionPlan,
    PlanStep,
    StepStatus,
)
from .plan_parsing import _PLANNING_PROMPT
from .plan_step_turn import PlanStepTurnMixin

logger = get_logger(__name__)


class HybridLoop(PlanStepTurnMixin):
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
        steering_inbox: Optional source of operator steering directives,
            polled at each turn boundary and mid-stream; ``None`` disables
            steering for the run.
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

        Raises:
            ValueError: When the resolved executor or planner model id is
                blank, so a misconfigured run fails at its first boundary
                rather than several turns into the provider calls.
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
        cancel_result = await check_task_cancelled(ctx, task_cancellation_checker, [])
        if cancel_result is not None:
            return self._finalize(cancel_result, [], 0)
        default_model = ctx.identity.model.model_id
        try:
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
        except ValueError as exc:
            # Fails loud, but the run object does not exist yet to carry the
            # id, so name the run here before the error leaves the loop.
            logger.error(
                EXECUTION_LOOP_ERROR,
                execution_id=ctx.execution_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
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
            await self._announce_step(run, state, step)

            step_start = len(state.turns)
            step_result = await self._execute_step(run, state, step)

            if isinstance(step_result, ExecutionResult):
                # The in-flight step ends here (cancel / shutdown / budget /
                # stagnation / error). Classify it too so its signal is not
                # dropped from quality_signals, which the worker health
                # pipeline consumes downstream.
                await self._record_step_signal(
                    signals,
                    state,
                    step_start,
                    step_result.termination_reason,
                    skip_when_empty=True,
                )
                return self._attach_signals(
                    self._finalize(
                        step_result,
                        state.all_plans,
                        state.replans_used,
                    ),
                    signals,
                )

            await self._record_step_signal(
                signals,
                state,
                step_start,
                step_result.signal_reason,
            )

            if step_result.step_succeeded:
                terminal = await self._settle_completed_step(run, state, step)
                if terminal is not None:
                    return self._attach_signals(terminal, signals)
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
            # The failure replan already incorporates any adopted directive
            # (it sits in the conversation), so clear the pending steering
            # replan rather than let it fire a redundant second replan at the
            # next boundary.
            state.ctx = clear_superseded_directive(
                state, trigger=ReplanTrigger.STEP_FAILURE
            )
            state.restart_plan()

        return self._attach_signals(self._build_final_result(state), signals)

    async def _announce_step(
        self,
        run: StepRunContext,
        state: StepRunState,
        step: PlanStep,
    ) -> None:
        """Mark the current step in progress and publish its start."""
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

    async def _record_step_signal(
        self,
        signals: list[StepQualitySignal],
        state: StepRunState,
        step_start: int,
        reason: TerminationReason,
        *,
        skip_when_empty: bool = False,
    ) -> None:
        """Classify the turns this step produced and accumulate its signal.

        Args:
            signals: Accumulator the produced signal is appended to.
            state: Run state, read for the step index and turn history.
            step_start: Index into ``state.turns`` where this step began.
            reason: Termination reason to classify the step under.
            skip_when_empty: Skip classification when the step produced no
                turns at all, which a terminal outcome can do.
        """
        step_turns = tuple(state.turns[step_start:])
        if skip_when_empty and not step_turns:
            return
        step_signal = await classify_step(
            self._step_classifier,
            step_index=state.step_idx,
            step_turns=step_turns,
            termination_reason=reason,
        )
        if step_signal is not None:
            signals.append(step_signal)

    async def _settle_completed_step(
        self,
        run: StepRunContext,
        state: StepRunState,
        step: PlanStep,
    ) -> ExecutionResult | None:
        """Handle a successful step and position the cursor for the next one.

        Returns:
            A terminal :class:`ExecutionResult` when completion handling halts
            the run, or ``None`` once the cursor has been positioned.
        """
        outcome = await self._handle_completed_step(run, state, step)
        if isinstance(outcome, ExecutionResult):
            return outcome

        restart = outcome.wants_replan
        if not restart and state.ctx.pending_steering_replan_id is not None:
            # A REDIRECT adopted mid-step forces a replan at this safe
            # boundary so the revised plan honours the directive.
            steer_out = await self._steering_replan_hybrid(run, state, step)
            if steer_out is not None:
                return steer_out
            restart = True
        elif restart and state.ctx.pending_steering_replan_id is not None:
            # A completion-triggered replan already re-planned with the
            # adopted directive in conversation context, so a dedicated
            # steering replan would be redundant. Clear the pending flag so
            # it does not linger to fire a stale replan on a later step or
            # persist into a terminal checkpoint.
            state.ctx = clear_superseded_directive(
                state, trigger=ReplanTrigger.COMPLETION_SUMMARY
            )

        if restart:
            state.restart_plan()
        else:
            state.advance_step()
        return None

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
    ) -> ReplanVerdict | ExecutionResult:
        """Handle a completed step: update status, checkpoint, replan.

        Returns:
            Either a terminal :class:`ExecutionResult` when the progress
            summary halts the loop, or the :class:`ReplanVerdict` telling the
            outer loop whether to begin again from step 0 after a replan.
        """
        state.plan = update_step_status(
            state.plan,
            state.step_idx,
            StepStatus.COMPLETED,
        )
        state.sync_current_plan()
        logger.info(
            EXECUTION_PLAN_STEP_COMPLETE,
            execution_id=state.ctx.execution_id,
            step_number=step.step_number,
        )

        if not self._config.checkpoint_after_each_step:
            return ReplanVerdict.PROCEED

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
            summary=summary_result,
        )

    async def _decide_replan_on_completion(
        self,
        run: StepRunContext,
        state: StepRunState,
        step: PlanStep,
        *,
        summary: ReplanVerdict,
    ) -> ReplanVerdict | ExecutionResult:
        """Decide whether to replan after a successful step.

        Returns:
            The :class:`ReplanVerdict` the outer loop acts on, or an
            :class:`ExecutionResult` for termination conditions.
        """
        if not (
            summary.wants_replan
            and self._config.allow_replan_on_completion
            and state.replans_used < self._config.max_replans
            and state.step_idx < len(state.plan.steps) - 1
            and state.ctx.has_turns_remaining
        ):
            return ReplanVerdict.PROCEED

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
            trigger=ReplanTrigger.COMPLETION_SUMMARY,
        )
        if isinstance(replan_result, ExecutionResult):
            return self._finalize(
                replan_result,
                state.all_plans,
                state.replans_used,
            )
        state.record_replan(replan_result)
        logger.info(
            EXECUTION_HYBRID_REPLAN_DECIDED,
            execution_id=state.ctx.execution_id,
            trigger="completion_summary",
            step_number=step.step_number,
            replans_used=state.replans_used,
        )
        return ReplanVerdict.REPLAN

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
        # Read the directive id before the clear below, or the log records
        # ``None`` for the very field that identifies the directive.
        directive_id = state.ctx.pending_steering_replan_id
        result = await do_replan(
            self._config,
            run,
            state,
            step,
            trigger=ReplanTrigger.STEERING,
        )
        if isinstance(result, ExecutionResult):
            return self._finalize(result, state.all_plans, state.replans_used)
        state.record_replan(result, counts_against_budget=False)
        state.ctx = state.ctx.cleared_pending_replan()
        logger.info(
            EXECUTION_HYBRID_REPLAN_DECIDED,
            execution_id=state.ctx.execution_id,
            trigger="steering",
            directive_id=directive_id,
            step_number=step.step_number,
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
        state.sync_current_plan()

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
    ) -> StepTurnOutcome | ExecutionResult:
        """Execute a single plan step via a mini-ReAct sub-loop.

        Returns:
            ``STEP_SUCCEEDED`` or ``STEP_FAILED`` once the step concludes
            (never ``CONTINUE``, which the sub-loop consumes here), or an
            :class:`ExecutionResult` for termination.
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
            if result is not StepTurnOutcome.CONTINUE:
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

        # Loop exited without step completion: the run's turn budget or the
        # per-step one ran out, which is a different failure from a step the
        # model concluded unsuccessfully.
        if not state.ctx.has_turns_remaining:
            return StepTurnOutcome.STEP_EXHAUSTED
        logger.warning(
            EXECUTION_HYBRID_STEP_TURN_LIMIT,
            execution_id=state.ctx.execution_id,
            step_number=step.step_number,
            max_turns_per_step=self._config.max_turns_per_step,
        )
        return StepTurnOutcome.STEP_EXHAUSTED

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
