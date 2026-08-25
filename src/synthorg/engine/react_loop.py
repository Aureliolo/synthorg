"""ReAct execution loop -- think, act, observe.

Implements the ``ExecutionLoop`` protocol using the ReAct pattern:
check shutdown -> check budget -> call LLM -> record turn ->
check for LLM errors -> update context -> handle completion or
(check shutdown -> execute tools) -> repeat.
"""

from typing import Self

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.completion_enums import FinishReason
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.checkpoint.callback import CheckpointCallback
from synthorg.engine.compaction.protocol import CompactionCallback
from synthorg.engine.intervention.inbox import SteeringInbox
from synthorg.engine.quality.classifier import StepQualityClassifier
from synthorg.engine.response_budget import DEFAULT_AGENT_MAX_RESPONSE_TOKENS
from synthorg.engine.resume_scope import is_resumed_run
from synthorg.engine.stagnation.protocol import StagnationDetector
from synthorg.execution.turn import TurnRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_CHECKPOINT_CALLBACK_FAILED,
    EXECUTION_LOOP_ERROR,
    EXECUTION_LOOP_START,
    EXECUTION_LOOP_TERMINATED,
    EXECUTION_LOOP_TURN_COMPLETE,
)
from synthorg.providers.models import (
    CompletionConfig,
    CompletionResponse,
    ToolDefinition,
)
from synthorg.providers.protocol import CompletionProvider
from synthorg.tools.protocol import ToolInvokerProtocol

from .context import AgentContext
from .intervention.loop_hook import check_steering
from .loop_cancellation import check_task_cancelled
from .loop_control_helpers import (
    check_budget,
    check_shutdown,
    check_stagnation,
    invoke_compaction,
)
from .loop_empty_run import delivered_nothing, nudge_empty_run
from .loop_helpers import (
    build_result,
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
    TurnObserver,
)
from .loop_quality_signals import attach_whole_run_signals
from .loop_silent_turn import continue_silent_turn
from .loop_streaming import (
    InterruptWatch,
    _TurnInterrupted,
    fold_interrupt_usage,
    run_provider_turn,
)
from .loop_tool_execution import (
    clear_last_turn_tool_calls,
    execute_tool_calls,
)
from .loop_turn_budget import ceiling_result, grant_extension
from .loop_turn_observer import notify_turn_observer
from .loop_unresolved_tools import unresolved_tools_result
from .loop_unusable_turn import (
    continue_unusable_turn,
    is_unusable_turn,
    unusable_turn_error,
)

logger = get_logger(__name__)


class ReactLoop:
    """ReAct execution loop: reason, act, observe.

    The loop checks for shutdown, checks the budget, calls the LLM,
    checks for termination conditions, executes any requested tools,
    feeds results back, and repeats until the LLM signals completion,
    the turn limit is reached, the budget is exhausted, a shutdown is
    requested, or an error occurs.

    Args:
        checkpoint_callback: Optional async callback invoked after each
            completed turn; the callback itself decides whether to persist.
        approval_gate: Optional gate that checks for pending escalations
            after tool execution and parks the agent when approval is
            required.  ``None`` disables approval checks.
        stagnation_detector: Optional detector that checks for
            repetitive tool-call patterns and intervenes with
            corrective prompts or early termination.  ``None``
            disables stagnation detection.
        compaction_callback: Optional async callback invoked at turn
            boundaries to compress older conversation turns when the
            context fill level is high.  ``None`` disables compaction.
        step_classifier: Optional step-quality classifier. ReAct is
            turn-based with no step boundary, so a single whole-run
            signal is emitted at natural termination; ``None`` disables
            quality classification.
        steering_inbox: Optional inbox polled at turn boundaries for
            mid-run steering messages; ``None`` disables steering.
        turn_observer: Optional async callback invoked after each
            continuing turn with the tools it requested; ``None``
            disables it. Purely observational.
    """

    def __init__(
        self,
        checkpoint_callback: CheckpointCallback | None = None,
        *,
        approval_gate: ApprovalGate | None = None,
        stagnation_detector: StagnationDetector | None = None,
        compaction_callback: CompactionCallback | None = None,
        steering_inbox: SteeringInbox | None = None,
        step_classifier: StepQualityClassifier | None = None,
        turn_observer: TurnObserver | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._checkpoint_callback = checkpoint_callback
        self._approval_gate = approval_gate
        self._stagnation_detector = stagnation_detector
        self._compaction_callback = compaction_callback
        self._steering_inbox = steering_inbox
        self._step_classifier = step_classifier
        self._turn_observer = turn_observer
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def _attach_whole_run_signals(
        self,
        result: ExecutionResult,
        turns: list[TurnRecord],
    ) -> ExecutionResult:
        """Attach this run's quality signal to a terminating result.

        Returns:
            The result with ``quality_signals`` populated, or unchanged
            when the run produced no turns to classify.
        """
        return await attach_whole_run_signals(result, turns, self._step_classifier)

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

    @property
    def step_classifier(self) -> StepQualityClassifier | None:
        """Return the step-quality classifier, or ``None``."""
        return self._step_classifier

    def with_checkpoint_callback(self, callback: CheckpointCallback) -> Self:
        """Return a copy of this loop that also checkpoints.

        The single owner of what a rebuilt loop carries. Resume rebuilds the
        loop to attach a callback the engine could not supply at construction,
        and a rebuild that names its fields at the call site is a rebuild that
        drops whichever control the next one adds.

        Rebuilt through ``type(self)`` rather than this class by name, because
        the resume path reaches here for any loop passing ``isinstance``, and a
        loop supplied from outside that specialises this one would otherwise
        come back as the base and resume without whatever it specialised.

        Returns:
            A new loop of this one's own type carrying every control of this
            one plus ``callback``.
        """
        return type(self)(
            callback,
            approval_gate=self._approval_gate,
            stagnation_detector=self._stagnation_detector,
            compaction_callback=self._compaction_callback,
            steering_inbox=self._steering_inbox,
            step_classifier=self._step_classifier,
            turn_observer=self._turn_observer,
            clock=self._clock,
        )

    def get_loop_type(self) -> str:
        """Return the loop type identifier."""
        return "react"

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
        """Run the ReAct loop until termination.

        Args:
            context: Initial agent context with conversation.
            provider: LLM completion provider.
            tool_invoker: Optional tool invoker for tool execution.
            budget_checker: Optional budget exhaustion callback.
            shutdown_checker: Optional callback; returns ``True`` when
                a graceful shutdown has been requested.
            completion_config: Optional per-execution config override.
            task_cancellation_checker: Optional async callback; returns
                ``True`` when the task was cancelled/superseded externally.
            turn_observer: Optional per-run progress callback; when given,
                it takes precedence over the construction-time observer so
                a per-execution stream (e.g. AG-UI task progress) can be
                wired without rebuilding the shared loop.
            streaming_enabled: When ``True``, each per-turn LLM call streams
                and is interruptible mid-flight (operator cancellation and
                steering REDIRECT); otherwise a non-streaming call is used.

        Returns:
            Execution result with final context and termination info.

        Raises:
            MemoryError: Re-raised unconditionally (non-recoverable).
            RecursionError: Re-raised unconditionally (non-recoverable).
        """
        model_id, config, tool_defs, turns = self._prepare_loop(
            context, completion_config, tool_invoker
        )
        ctx = context
        corrections_injected = 0
        effective_observer = turn_observer or self._turn_observer

        # Bounded by the turn budget and its extensions; every iteration
        # re-checks shutdown, task cancellation and the cost budget below.
        # lint-allow: long-running-loop-kill-switch -- turn-budget bounded
        while True:
            if not ctx.has_turns_remaining:
                # The ceiling is a backstop against a pathological loop, not
                # a verdict on work that is taking longer than the estimate.
                # Carry on while there are extensions left; park only once
                # they are spent, so nothing is discarded either way.
                extended = grant_extension(ctx, turns)
                if extended is None:
                    break
                ctx = extended
            shutdown_result = check_shutdown(ctx, shutdown_checker, turns)
            if shutdown_result is not None:
                return await self._attach_whole_run_signals(shutdown_result, turns)

            budget_result = check_budget(ctx, budget_checker, turns)
            if budget_result is not None:
                return await self._attach_whole_run_signals(budget_result, turns)

            cancel_result = await check_task_cancelled(
                ctx, task_cancellation_checker, turns
            )
            if cancel_result is not None:
                return await self._attach_whole_run_signals(cancel_result, turns)

            # Adopt any pending steering directives before the LLM call so
            # the operator's constraint is in context for this turn.
            steered = await check_steering(ctx, self._steering_inbox)
            if steered is not None:
                ctx = steered

            # Refresh tool defs each turn so newly loaded tools appear
            tool_defs = get_tool_definitions(tool_invoker, ctx.loaded_tools)

            turn_number = ctx.turn_count + 1
            outcome = await run_provider_turn(
                ctx,
                provider,
                model_id,
                tool_defs=tool_defs,
                config=config,
                turns=turns,
                streaming_enabled=streaming_enabled,
                watch=InterruptWatch(
                    cancellation_checker=task_cancellation_checker,
                    steering_inbox=self._steering_inbox,
                    clock=self._clock,
                ),
            )
            if isinstance(outcome, ExecutionResult):
                return await self._attach_whole_run_signals(outcome, turns)
            if isinstance(outcome, _TurnInterrupted):
                # A steering REDIRECT aborted the in-flight call; fold the
                # partial usage and re-issue the turn so the top-of-loop
                # steering check adopts the directive into context.
                ctx = fold_interrupt_usage(ctx, outcome)
                continue
            response = outcome

            turns.append(
                make_turn_record(
                    turn_number,
                    response,
                    call_category=classify_turn(turn_number, response, ctx),
                    provider_metadata=response.provider_metadata,
                )
            )

            result = await self._process_turn_response(
                ctx,
                response,
                turn_number=turn_number,
                turns=turns,
                tool_invoker=tool_invoker,
                shutdown_checker=shutdown_checker,
            )
            if isinstance(result, ExecutionResult):
                return await self._attach_whole_run_signals(result, turns)
            ctx = result

            await notify_turn_observer(turn_number, response, effective_observer, ctx)

            # Before the fingerprint detector, because this signal survives
            # drifting arguments: a turn whose every tool call resolved to
            # nothing ran nothing, and a run doing only that has no way back.
            unresolved = unresolved_tools_result(ctx, turns)
            if unresolved is not None:
                return await self._attach_whole_run_signals(unresolved, turns)

            # Stagnation detection after successful turn processing
            stag_outcome = await check_stagnation(
                ctx,
                self._stagnation_detector,
                turns,
                corrections_injected,
            )
            if isinstance(stag_outcome, ExecutionResult):
                return await self._attach_whole_run_signals(stag_outcome, turns)
            if isinstance(stag_outcome, tuple):
                ctx, corrections_injected = stag_outcome

            # Context compaction at turn boundaries
            compacted = await invoke_compaction(
                ctx,
                self._compaction_callback,
                turn_number,
            )
            if compacted is not None:
                ctx = compacted

        return await self._attach_whole_run_signals(
            ceiling_result(ctx, turns),
            turns,
        )

    def _prepare_loop(
        self,
        context: AgentContext,
        completion_config: CompletionConfig | None,
        tool_invoker: ToolInvokerProtocol | None,
    ) -> tuple[str, CompletionConfig, list[ToolDefinition] | None, list[TurnRecord]]:
        """Log loop start and resolve config, model ID, and tool defs.

        Returns:
            ``(model_id, config, tool_defs, turns)``: the resolved
            model id, the effective :class:`CompletionConfig`, the
            tool definitions for the loop (``None`` when no invoker),
            and an empty turn-record list to accumulate into.
        """
        logger.info(
            EXECUTION_LOOP_START,
            execution_id=context.execution_id,
            loop_type=self.get_loop_type(),
            max_turns=context.max_turns,
        )
        model_id = context.identity.model.model_id
        # A caller that hands us no config has not been through
        # `_fold_response_budget`, so the identity's own `max_tokens` is
        # whatever the operator bound and is `None` for every agent that bound
        # nothing. `None` does NOT mean the same thing here as it does on the
        # binding: the driver omits the key entirely, so it reaches the
        # provider as no ceiling at all rather than as "resolve me". A floor
        # keeps that one meaning out of the request; the resolver still wins
        # wherever it ran, because a resolved config never reaches this branch.
        config = completion_config or CompletionConfig(
            temperature=context.identity.model.temperature,
            max_tokens=(
                context.identity.model.max_tokens or DEFAULT_AGENT_MAX_RESPONSE_TOKENS
            ),
        )
        return (
            model_id,
            config,
            get_tool_definitions(tool_invoker, context.loaded_tools),
            [],
        )

    async def _process_turn_response(
        self,
        ctx: AgentContext,
        response: CompletionResponse,
        *,
        turn_number: int,
        turns: list[TurnRecord],
        tool_invoker: ToolInvokerProtocol | None,
        shutdown_checker: ShutdownChecker | None = None,
    ) -> AgentContext | ExecutionResult:
        """Check errors, update context, handle completion or tool calls.

        Returns:
            The updated :class:`AgentContext` when the loop should
            continue, or an :class:`ExecutionResult` when this turn
            terminates the loop (completion, error, shutdown, or
            tool-execution outcome).
        """
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

        # Checkpoint is saved after the LLM response is recorded but
        # before tool execution.  This is intentional: if a crash
        # happens during tool execution, the agent resumes with the
        # LLM response and can detect whether tools already ran.  The
        # alternative (after tools) would lose the entire LLM call on
        # a mid-tool crash.  Tools should be idempotent by design.
        if self._checkpoint_callback is not None:
            try:
                await self._checkpoint_callback(ctx)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                # lint-allow: swallow-ok -- resiliency side channel
                reraise_critical(exc)
                logger.warning(
                    EXECUTION_CHECKPOINT_CALLBACK_FAILED,
                    execution_id=ctx.execution_id,
                    turn=turn_number,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        if not response.tool_calls:
            resumed = continue_silent_turn(ctx, response, turn_number)
            if resumed is not None:
                return resumed
            retried = continue_unusable_turn(ctx, response, turn_number)
            if retried is not None:
                return retried
            if is_unusable_turn(response):
                # Out of corrections, so the run ends here -- as an error. The
                # turn produced nothing, and falling through would reach the
                # ordinary completion path and report a run that delivered
                # nothing as a success.
                error_msg = unusable_turn_error(turn_number)
                logger.error(
                    EXECUTION_LOOP_ERROR,
                    execution_id=ctx.execution_id,
                    turn=turn_number,
                    error=error_msg,
                )
                return build_result(
                    ctx,
                    TerminationReason.ERROR,
                    turns,
                    error_message=error_msg,
                )
            nudged = nudge_empty_run(ctx, turns, turn_number)
            if nudged is not None:
                return nudged
            return await self._handle_completion(ctx, response, turns)

        # Check shutdown before tool invocations
        shutdown_result = check_shutdown(ctx, shutdown_checker, turns)
        if shutdown_result is not None:
            clear_last_turn_tool_calls(turns)
            # Rebuild with cleaned turns (shutdown_result snapshot'd old turns)
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

    async def _handle_completion(
        self,
        ctx: AgentContext,
        response: CompletionResponse,
        turns: list[TurnRecord],
    ) -> ExecutionResult:
        """Handle a no-tool-call response: normal completion, or a silent no-op.

        Only reachable for a turn that produced usable output:
        :func:`is_unusable_turn` intercepts a ``TOOL_USE`` finish carrying no
        tool call before the caller gets here, and ends the run itself once the
        corrections are spent.

        Returns:
            An :class:`ExecutionResult` with ``termination_reason=NO_OP`` when
            a task that declared artifacts delivered none, or ``COMPLETED``
            for the normal text-response completion.
        """
        # Fail-loud on a silent no-op: a WORK task (one that declared
        # expected artifacts) that finished without calling a tool that could
        # deliver produced zero artifacts. Chat actions (no ``task_execution``)
        # and tasks that expect no deliverable legitimately answer in text, so
        # only artifact-expecting empty runs are reclassified from COMPLETED
        # to NO_OP (routed to FAILED downstream unless justified). A resumed
        # run only sees this segment's turns, so its count is not a valid proxy
        # for total output (earlier segments may have produced artifacts before
        # an approval park); leave it COMPLETED.
        if (
            ctx.task_execution is not None
            and ctx.task_execution.task.artifacts_expected
            and delivered_nothing(turns)
            and not is_resumed_run()
        ):
            no_op_msg = (
                "Task run produced no artifacts: the agent finished without "
                "calling any tool that could produce one. A silent no-op "
                "success is a failure."
            )
            logger.warning(
                EXECUTION_LOOP_TERMINATED,
                execution_id=ctx.execution_id,
                reason=TerminationReason.NO_OP.value,
                turns=len(turns),
                artifacts_expected=True,
                note=no_op_msg,
            )
            return build_result(
                ctx,
                TerminationReason.NO_OP,
                turns,
                error_message=no_op_msg,
            )
        if response.finish_reason == FinishReason.MAX_TOKENS:
            logger.warning(
                EXECUTION_LOOP_TERMINATED,
                execution_id=ctx.execution_id,
                reason=TerminationReason.COMPLETED.value,
                turns=len(turns),
                truncated=True,
            )
        else:
            logger.info(
                EXECUTION_LOOP_TERMINATED,
                execution_id=ctx.execution_id,
                reason=TerminationReason.COMPLETED.value,
                turns=len(turns),
            )
        return build_result(
            ctx,
            TerminationReason.COMPLETED,
            turns,
        )
