"""Task status sync -- AgentEngine → TaskEngine integration.

Module-level functions extracted from ``AgentEngine`` to keep the
orchestrator file focused on execution flow.  Remote ``TaskEngine`` sync
is best-effort (failures are logged and swallowed so agent execution is
never blocked by a ``TaskEngine`` issue), but the post-execution
transition itself is fail-loud: a work task that produced no artifacts
and no recorded no-op justification is driven to ``FAILED`` rather than
pushed to review as a silent no-op success.
"""

from typing import TYPE_CHECKING, Final

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.run_outcome import RunOutcome
from synthorg.core.task_enums import TaskStatus
from synthorg.engine._task_sync_engine import sync_to_task_engine
from synthorg.engine.context import AgentContext
from synthorg.engine.errors import ExecutionStateError
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.resume_scope import is_resumed_run
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_sync_review import create_review_approval
from synthorg.observability import get_logger, safe_error_description

if TYPE_CHECKING:
    # Cycle breaker: ``review_gate`` imports this module (``sync_to_task_engine``),
    # so the auto-review types are resolved only for annotations. The runtime
    # call site duck-types through the passed-in gate.
    from synthorg.engine.review.pipeline import ReviewPipeline
    from synthorg.engine.review_gate import ReviewGateService
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_ERROR,
    EXECUTION_ENGINE_NO_ARTIFACTS_FAILED,
    EXECUTION_ENGINE_TASK_TRANSITION,
)

logger = get_logger(__name__)

# Stepwise completion transitions: each (target_status, reason) pair
# is applied in order.  ``apply_post_execution_transitions`` updates
# ``ctx`` after each step so partial-failure always reflects the
# furthest-reached state.
_COMPLETION_STEPS: tuple[tuple[TaskStatus, str], ...] = (
    (TaskStatus.IN_REVIEW, "Agent completed execution -- awaiting review"),
)

# Reason surfaced when a work task finishes with no produced artifacts and
# no recorded no-op justification: the run is failed rather than pushed to
# review as a silent no-op success.
_EMPTY_RUN_REASON: Final[str] = (
    "Run produced no artifacts and no tool calls; failing the task instead "
    "of recording a silent no-op success"
)

# Extension point for a legitimately empty run (e.g. a task that concluded no
# change was needed): its presence routes an otherwise-empty run to review
# instead of FAILED. The invariant is fail-closed today -- no production path
# sets this key, so an empty work run always fails. When a producer is wired,
# it MUST be a system/pipeline-set, validated signal, never a value derived
# from agent/LLM output, so an agent cannot self-justify an empty run.
_NO_OP_JUSTIFICATION_KEY: Final[str] = "no_op_justification"


async def transition_task_if_needed(
    ctx: AgentContext,
    agent_id: str,
    task_id: str,
    task_engine: TaskEngine | None,
) -> AgentContext:
    """Transition ASSIGNED -> IN_PROGRESS; pass through IN_PROGRESS.

    Also syncs the transition to TaskEngine (best-effort).

    Returns:
        The (possibly updated) :class:`AgentContext` with status
        transitioned to ``IN_PROGRESS`` when the entry status was
        ``ASSIGNED``; otherwise ``ctx`` unchanged.
    """
    if (
        ctx.task_execution is not None
        and ctx.task_execution.status == TaskStatus.ASSIGNED
    ):
        ctx, _ = await _transition_and_sync(
            ctx,
            target_status=TaskStatus.IN_PROGRESS,
            reason="Engine starting execution",
            agent_id=agent_id,
            task_id=task_id,
            task_engine=task_engine,
            critical=True,
        )
    return ctx


async def apply_post_execution_transitions(  # noqa: PLR0913 -- post-exec collaborators
    execution_result: ExecutionResult,
    agent_id: str,
    task_id: str,
    task_engine: TaskEngine | None,
    approval_store: ApprovalStoreProtocol | None = None,
    review_gate: ReviewGateService | None = None,
    review_pipeline: ReviewPipeline | None = None,
) -> ExecutionResult:
    """Apply post-execution task transitions based on termination reason.

    COMPLETED termination triggers the stepwise transitions defined
    in ``_COMPLETION_STEPS`` (currently: -> IN_REVIEW, awaiting review).
    SHUTDOWN triggers current status -> INTERRUPTED.
    A ``NO_OP`` run -- or a ``COMPLETED`` run that a work task finished
    with zero tool calls (the silent-no-op proxy for zero artifacts) --
    is driven to FAILED instead of review, unless a no-op justification
    was recorded or the run resumed prior work (see ``empty_run_fails``).
    Each transition is synced to TaskEngine incrementally.
    Transition failures are logged but never discard the result.
    ``MemoryError`` and ``RecursionError`` propagate unconditionally.

    When an ``approval_store`` is provided and the task reaches
    IN_REVIEW, an ``ApprovalItem`` is created so the human knows
    there is a task to review.

    Returns:
        The original ``execution_result`` unchanged if no transitions
        apply, or a copy with updated context reflecting the
        furthest-reached state on success or partial failure.
    """
    ctx = execution_result.context
    if ctx.task_execution is None:
        return execution_result

    reason = execution_result.termination_reason

    if reason == TerminationReason.SHUTDOWN:
        return await _transition_to_interrupted(
            execution_result, ctx, agent_id, task_id, task_engine
        )

    if reason == TerminationReason.PARKED and (
        execution_result.metadata.get("clarification") is True
        or execution_result.metadata.get("decision") is True
    ):
        # Both a clarification question and an execution-time decision fork
        # wait on the operator, so the task parks in AWAITING_INPUT until the
        # human answers / picks an option; the resume path moves it back.
        return await _transition_to_awaiting_input(
            execution_result, ctx, agent_id, task_id, task_engine
        )

    justified = bool(execution_result.metadata.get(_NO_OP_JUSTIFICATION_KEY))
    task_expects_artifacts = bool(ctx.task_execution.task.artifacts_expected)
    # A resumed/replayed run only carries the current segment's turns, so its
    # zero-tool-call count is not a valid proxy for total task output: earlier
    # segments (before an approval park) may already have produced artifacts.
    # Exempt a continued run from the empty-run failure so a legitimately
    # progressed task is never discarded; a genuinely empty continued run
    # still completes to review rather than FAILED.
    empty_run_fails = not is_resumed_run()

    # A silent no-op success is a failure: a WORK task (one that declared
    # expected artifacts) that produced none (proxied by zero tool calls) is
    # failed unless an explicit no-op justification was recorded. Enforced in
    # two layers: the react loop classifies the empty run as NO_OP, and this
    # transition also guards a COMPLETED that slipped through from another loop.
    if reason == TerminationReason.NO_OP and not justified and empty_run_fails:
        return await _transition_to_failed(
            execution_result, ctx, agent_id, task_id, task_engine, approval_store
        )

    if reason not in (TerminationReason.COMPLETED, TerminationReason.NO_OP):
        return execution_result

    if (
        task_expects_artifacts
        and execution_result.total_tool_calls == 0
        and not justified
        and empty_run_fails
    ):
        return await _transition_to_failed(
            execution_result, ctx, agent_id, task_id, task_engine, approval_store
        )

    return await _transition_to_review(
        execution_result,
        ctx,
        agent_id,
        task_id,
        task_engine,
        approval_store,
        review_gate,
        review_pipeline,
    )


async def _maybe_auto_review(
    review_gate: ReviewGateService | None,
    review_pipeline: ReviewPipeline | None,
    *,
    agent_id: str,
    task_id: str,
) -> None:
    """Run the staged review pipeline on completion, if auto-review is wired.

    Best-effort: a wired gate + pipeline (only present when the operator
    enabled ``engine.auto_review_on_completion``) drives the task's verdict
    without waiting for a human. A failure is logged and swallowed so a
    review-pipeline fault never discards the agent's completed work -- the
    task simply stays in IN_REVIEW for a human, exactly as when auto-review
    is off.

    Raises:
        MemoryError: Propagated unconditionally (non-recoverable).
        RecursionError: Propagated unconditionally (non-recoverable).
    """
    if review_gate is None or review_pipeline is None:
        return
    try:
        await review_gate.run_pipeline(
            task_id=task_id,
            pipeline=review_pipeline,
            decided_by="system:auto-review",
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:  # noqa: BLE001 -- best-effort: never block completion
        # lint-allow: swallow-ok -- best-effort side channel
        reraise_critical(exc)
        logger.warning(
            EXECUTION_ENGINE_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            context="Automatic review pipeline failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _transition_and_sync(  # noqa: PLR0913
    ctx: AgentContext,
    *,
    target_status: TaskStatus,
    reason: str,
    agent_id: str,
    task_id: str,
    task_engine: TaskEngine | None,
    critical: bool = False,
) -> tuple[AgentContext, bool]:
    """Apply a local task transition, log it, and sync to TaskEngine.

    The local transition (via ``with_task_transition``) is applied
    unconditionally; the remote sync is best-effort.

    Returns:
        The updated :class:`AgentContext` after the local transition, and
        whether the central engine now reflects the transition (see
        :func:`sync_to_task_engine`).
    """
    prev_status = ctx.task_execution.status  # type: ignore[union-attr]
    ctx = ctx.with_task_transition(target_status, reason=reason)
    logger.info(
        EXECUTION_ENGINE_TASK_TRANSITION,
        agent_id=agent_id,
        task_id=task_id,
        from_status=prev_status.value,
        to_status=target_status.value,
    )
    synced = await sync_to_task_engine(
        task_engine,
        target_status=target_status,
        task_id=task_id,
        agent_id=agent_id,
        reason=reason,
        critical=critical,
    )
    return ctx, synced


async def _transition_to_review(  # noqa: PLR0913 -- post-exec collaborators
    execution_result: ExecutionResult,
    ctx: AgentContext,
    agent_id: str,
    task_id: str,
    task_engine: TaskEngine | None,
    approval_store: ApprovalStoreProtocol | None,
    review_gate: ReviewGateService | None,
    review_pipeline: ReviewPipeline | None,
) -> ExecutionResult:
    """Drive a COMPLETED run IN_PROGRESS -> IN_REVIEW, then request review.

    Applies ``_COMPLETION_STEPS`` stepwise so ``ctx`` always reflects the
    furthest-reached state even when one step raises (partial-completion
    safety). On reaching IN_REVIEW, a review approval is created and the
    auto-review pass runs when both are wired.

    Returns:
        The original ``execution_result`` when the context is unchanged, or
        a copy carrying the furthest-reached context.
    """
    synced = False
    for target, step_reason in _COMPLETION_STEPS:
        try:
            ctx, synced = await _transition_and_sync(
                ctx,
                target_status=target,
                reason=step_reason,
                agent_id=agent_id,
                task_id=task_id,
                task_engine=task_engine,
            )
        except (ValueError, ExecutionStateError) as exc:
            logger.warning(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                context="Post-execution transition failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            break

    # Only queue the review approval + auto-review once the central engine
    # reflects IN_REVIEW: a swallowed/rejected sync leaves the engine's task
    # IN_PROGRESS, and an approval (or an auto-review decision) against that
    # stale state would act on a status the engine has not applied.
    if (
        synced
        and ctx.task_execution is not None
        and ctx.task_execution.status == TaskStatus.IN_REVIEW
    ):
        # Emptiness is an artifact-count fact resolved truthfully at read time,
        # not a tool-call proxy that can disagree with it. The review approval
        # is created as a plain completion; the DTO's run summary derives the
        # SUCCEEDED/EMPTY outcome from the produced artifacts when the operator
        # opens the queue. The fail-loud path already routes a genuinely empty
        # work run to FAILED before reaching here.
        await create_review_approval(
            approval_store,
            agent_id=agent_id,
            task_id=task_id,
            task=ctx.task_execution.task,
            outcome=RunOutcome.SUCCEEDED,
        )
        await _maybe_auto_review(
            review_gate, review_pipeline, agent_id=agent_id, task_id=task_id
        )

    if ctx is execution_result.context:
        return execution_result
    return execution_result.model_copy(update={"context": ctx})


async def _transition_to_failed(  # noqa: PLR0913 -- post-exec collaborators
    execution_result: ExecutionResult,
    ctx: AgentContext,
    agent_id: str,
    task_id: str,
    task_engine: TaskEngine | None,
    approval_store: ApprovalStoreProtocol | None,
) -> ExecutionResult:
    """Transition an empty/no-op run IN_PROGRESS -> FAILED, then flag it.

    A work task that produced no artifacts must surface a visible failure
    with the reason, never a silent no-op success pushed to review. A
    FAILED-outcome review approval is created (risk escalated one level
    above the task's stakes, so never LOW) so the failure lands in the
    operator's approval queue as an unmistakable failure rather than being
    invisible.

    Returns:
        A copy of ``execution_result`` with the context updated to
        ``FAILED``; the original is returned unchanged when the
        transition raises.
    """
    try:
        ctx, synced = await _transition_and_sync(
            ctx,
            target_status=TaskStatus.FAILED,
            reason=_EMPTY_RUN_REASON,
            agent_id=agent_id,
            task_id=task_id,
            task_engine=task_engine,
            critical=True,
        )
    except (ValueError, ExecutionStateError) as exc:
        logger.warning(
            EXECUTION_ENGINE_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            context="Post-execution FAILED transition failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return execution_result
    # Emit the no-artifacts fail event only after FAILED is persisted, so
    # the record never claims a failure that the transition did not land.
    logger.warning(
        EXECUTION_ENGINE_NO_ARTIFACTS_FAILED,
        agent_id=agent_id,
        task_id=task_id,
        context="Empty run: no artifacts produced; task failed",
        reason=_EMPTY_RUN_REASON,
    )
    # Only queue the failure approval once the central engine reflects FAILED:
    # a swallowed/rejected sync leaves the engine's task IN_PROGRESS, and a
    # ``review:task_failed`` item pointing at an in-progress task would let a
    # later decision transition the wrong state.
    if synced and ctx.task_execution is not None:
        await create_review_approval(
            approval_store,
            agent_id=agent_id,
            task_id=task_id,
            task=ctx.task_execution.task,
            outcome=RunOutcome.FAILED,
        )
    return execution_result.model_copy(update={"context": ctx})


async def _transition_to_interrupted(
    execution_result: ExecutionResult,
    ctx: AgentContext,
    agent_id: str,
    task_id: str,
    task_engine: TaskEngine | None,
) -> ExecutionResult:
    """Transition task to INTERRUPTED on graceful shutdown.

    Returns:
        A copy of ``execution_result`` with the context updated to
        the ``INTERRUPTED`` status; the original ``execution_result``
        is returned unchanged when the transition raises.
    """
    try:
        ctx, _ = await _transition_and_sync(
            ctx,
            target_status=TaskStatus.INTERRUPTED,
            reason="Graceful shutdown requested",
            agent_id=agent_id,
            task_id=task_id,
            task_engine=task_engine,
        )
        return execution_result.model_copy(update={"context": ctx})
    except (ValueError, ExecutionStateError) as exc:
        logger.warning(
            EXECUTION_ENGINE_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            context="Post-execution INTERRUPTED transition failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return execution_result


async def _transition_to_awaiting_input(
    execution_result: ExecutionResult,
    ctx: AgentContext,
    agent_id: str,
    task_id: str,
    task_engine: TaskEngine | None,
) -> ExecutionResult:
    """Transition task to AWAITING_INPUT on a clarification / decision park.

    Only the IN_PROGRESS entry status is moved; any other status is
    left untouched (the park may have happened before the ASSIGNED ->
    IN_PROGRESS transition landed, in which case there is nothing to
    pause). The resume path moves AWAITING_INPUT back to IN_PROGRESS
    before re-entering the loop.

    Returns:
        A copy of ``execution_result`` with the context updated to
        ``AWAITING_INPUT``; the original is returned unchanged when the
        task is not IN_PROGRESS or when the transition raises.
    """
    task_exec = ctx.task_execution
    if task_exec is None or task_exec.status != TaskStatus.IN_PROGRESS:
        return execution_result
    try:
        ctx, _ = await _transition_and_sync(
            ctx,
            target_status=TaskStatus.AWAITING_INPUT,
            reason="Agent paused for human input",
            agent_id=agent_id,
            task_id=task_id,
            task_engine=task_engine,
        )
        return execution_result.model_copy(update={"context": ctx})
    except (ValueError, ExecutionStateError) as exc:
        logger.warning(
            EXECUTION_ENGINE_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            context="Post-execution AWAITING_INPUT transition failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return execution_result
