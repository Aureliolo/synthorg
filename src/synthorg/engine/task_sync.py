"""Task status sync -- AgentEngine → TaskEngine integration.

Module-level functions extracted from ``AgentEngine`` to keep the
orchestrator file focused on execution flow.  Remote ``TaskEngine`` sync
is best-effort (failures are logged and swallowed so agent execution is
never blocked by a ``TaskEngine`` issue), but the post-execution
transition itself is fail-loud: a work task that produced no artifacts
and no recorded no-op justification is driven to ``FAILED`` rather than
pushed to review as a silent no-op success.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.run_outcome import RunOutcome
from synthorg.core.task_enums import TaskStatus
from synthorg.engine._task_sync_transitions import (
    transition_and_sync,
    transition_to_awaiting_input,
    transition_to_interrupted,
)
from synthorg.engine.artifacts.expected_artifact_check import (
    ArtifactPresence,
    ExpectedArtifactProbe,
)
from synthorg.engine.context import AgentContext
from synthorg.engine.errors import ExecutionStateError
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.loop_turn_budget import TURN_CEILING_METADATA_KEY
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
    EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
    EXECUTION_ENGINE_ERROR,
    EXECUTION_ENGINE_NO_ARTIFACTS_FAILED,
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

# Reason surfaced when a work task declared artifacts and produced none of
# them. ``{paths}`` names the declared paths, so the operator reads what was
# promised rather than that something unnamed went wrong.
_MISSING_ARTIFACTS_REASON: Final[str] = (
    "Run produced none of its declared artifacts ({paths}); failing the task "
    "instead of sending an empty deliverable to review"
)

# A run that stopped without finishing is not a run that finished. Left
# untransitioned these sat at IN_PROGRESS forever: the stall derivation reads
# IN_PROGRESS as "still moving", so the initiative could never be replanned
# and never be completed. FAILED is both honest and retryable, and it is the
# status the stall derivation recognises once retries are spent.
_UNFINISHED_REASONS: Final[Mapping[TerminationReason, str]] = MappingProxyType(
    {
        TerminationReason.MAX_TURNS: (
            "Run hit its turn cap without finishing the task"
        ),
        TerminationReason.BUDGET_EXHAUSTED: (
            "Run exhausted its cost budget without finishing the task"
        ),
        TerminationReason.STAGNATION: (
            "Run stopped making progress without finishing the task"
        ),
    }
)

# Extension point for a legitimately empty run (e.g. a task that concluded no
# change was needed): its presence routes an otherwise-empty run to review
# instead of FAILED. The invariant is fail-closed today -- no production path
# sets this key, so an empty work run always fails. When a producer is wired,
# it MUST be a system/pipeline-set, validated signal, never a value derived
# from agent/LLM output, so an agent cannot self-justify an empty run.
_NO_OP_JUSTIFICATION_KEY: Final[str] = "no_op_justification"


@dataclass(frozen=True, slots=True)
class _Move:
    """The run a post-execution transition moves, and where it reports.

    Every transition needs the same six: the finished run and its context,
    who ran it, what it ran, the engine the move syncs to and the queue the
    resulting item lands in. They are named once here because passing them
    apart turned each call into six lines of plumbing around one word of
    intent.
    """

    execution_result: ExecutionResult
    ctx: AgentContext
    agent_id: str
    task_id: str
    task_engine: TaskEngine | None
    approval_store: ApprovalStoreProtocol | None


async def transition_task_if_needed(
    ctx: AgentContext,
    agent_id: str,
    task_id: str,
    task_engine: TaskEngine | None,
) -> AgentContext:
    """Transition ASSIGNED -> IN_PROGRESS; pass through IN_PROGRESS.

    Returns:
        The (possibly updated) :class:`AgentContext` with status
        transitioned to ``IN_PROGRESS`` when the entry status was
        ``ASSIGNED``; otherwise ``ctx`` unchanged.

    Raises:
        ExecutionStateError: When a wired engine did not apply the entry
            transition. The run would otherwise proceed against a row the
            central engine still holds at its previous status: every
            later sync is validated from that status, so the whole run
            reports into a lifecycle that never started. The caller's
            fatal boundary terminates the task FAILED, which is both
            honest and retryable.
    """
    if (
        ctx.task_execution is not None
        and ctx.task_execution.status == TaskStatus.ASSIGNED
    ):
        ctx, synced = await transition_and_sync(
            ctx,
            target_status=TaskStatus.IN_PROGRESS,
            reason="Engine starting execution",
            agent_id=agent_id,
            task_id=task_id,
            task_engine=task_engine,
            critical=True,
        )
        if not synced:
            msg = (
                f"Task {task_id} could not be moved to IN_PROGRESS in the "
                "central engine; refusing to run work the engine has no "
                "record of starting"
            )
            raise ExecutionStateError(msg)
    return ctx


async def apply_post_execution_transitions(
    execution_result: ExecutionResult,
    *,
    agent_id: str,
    task_id: str,
    task_engine: TaskEngine | None,
    approval_store: ApprovalStoreProtocol | None = None,
    review_gate: ReviewGateService | None = None,
    review_pipeline: ReviewPipeline | None = None,
    artifact_probe: ExpectedArtifactProbe | None = None,
) -> ExecutionResult:
    """Apply post-execution task transitions based on termination reason.

    COMPLETED termination triggers the stepwise transitions defined
    in ``_COMPLETION_STEPS`` (currently: -> IN_REVIEW, awaiting review).
    SHUTDOWN triggers current status -> INTERRUPTED.
    A ``NO_OP`` run -- or a ``COMPLETED`` run that a work task finished
    with zero tool calls (the silent-no-op proxy for zero artifacts), or
    one whose declared artifacts are all absent from the workspace --
    is driven to FAILED instead of review, unless a no-op justification
    was recorded. A resumed run is exempt from the turn-count proxy alone
    (see ``empty_run_fails``); the workspace still answers for it, because
    what an earlier segment produced is on disk either way.
    A run that stopped without finishing (turn cap, budget, stagnation)
    is driven to FAILED too, so it becomes retryable and, once retries
    are spent, visible to the stall derivation the replan trigger reads.
    Each transition is synced to TaskEngine incrementally.
    A transition failure is logged and leaves the result intact, except on
    the FAILED path: a task that could not be moved off ``IN_PROGRESS``
    reads as still moving to the stall derivation, so that one raises
    ``ExecutionStateError`` for the caller to act on rather than returning
    a result whose recorded state never happened.
    ``MemoryError`` and ``RecursionError`` propagate unconditionally.

    When an ``approval_store`` is provided and the task reaches
    IN_REVIEW, an ``ApprovalItem`` is created so the human knows
    there is a task to review.

    Args:
        execution_result: The finished run.
        agent_id: The agent that ran it.
        task_id: The task it ran.
        task_engine: Central engine to sync each transition to.
        approval_store: Queue the review / failure item lands in.
        review_gate: Auto-review gate, when the operator enabled it.
        review_pipeline: Staged review pipeline, paired with the gate.
        artifact_probe: Asks the project workspace whether the declared
            artifacts exist. ``None`` leaves the zero-tool-call proxy as
            the only empty-run signal.

    Returns:
        The original ``execution_result`` unchanged if no transitions
        apply, or a copy with updated context reflecting the
        furthest-reached state on success or partial failure.

    Raises:
        ExecutionStateError: When the FAILED transition cannot land.
            Callers release the run's checkpoints before letting it
            through, so an unmoved task does not also leak its rows.
    """
    ctx = execution_result.context
    if ctx.task_execution is None:
        return execution_result

    reason = execution_result.termination_reason

    if reason == TerminationReason.SHUTDOWN:
        return await transition_to_interrupted(
            execution_result, ctx, agent_id, task_id, task_engine
        )

    if reason == TerminationReason.PARKED and (
        execution_result.metadata.get("clarification") is True
        or execution_result.metadata.get("decision") is True
        or execution_result.metadata.get(TURN_CEILING_METADATA_KEY) is True
    ):
        # A clarification question, an execution-time decision fork and a
        # spent turn budget all wait on the operator, so the task parks in
        # AWAITING_INPUT until the human answers; the resume path moves it
        # back. Each arrives with its approval already durable: the first two
        # from the tool the agent called, the third from `arm_turn_ceiling_park`
        # upstream, which downgrades the run to MAX_TURNS rather than let it
        # park with nothing in the queue able to answer it.
        return await transition_to_awaiting_input(
            execution_result, ctx, agent_id, task_id, task_engine
        )

    move = _Move(
        execution_result=execution_result,
        ctx=ctx,
        agent_id=agent_id,
        task_id=task_id,
        task_engine=task_engine,
        approval_store=approval_store,
    )

    unfinished = _UNFINISHED_REASONS.get(reason)
    if unfinished is not None:
        return await _transition_to_failed(move, reason=unfinished)

    if reason not in (TerminationReason.COMPLETED, TerminationReason.NO_OP):
        return execution_result

    undelivered = await _failed_for_no_delivery(move, artifact_probe=artifact_probe)
    if undelivered is not None:
        return undelivered

    return await _transition_to_review(
        move, review_gate=review_gate, review_pipeline=review_pipeline
    )


async def _failed_for_no_delivery(
    move: _Move,
    *,
    artifact_probe: ExpectedArtifactProbe | None,
) -> ExecutionResult | None:
    """Fail a run that finished having delivered nothing, or return ``None``.

    One question asked three ways, weakest evidence first: the loop's own
    NO_OP classification, the zero-tool-call proxy, and finally the
    workspace itself. Kept together because they are one decision -- did
    this run produce what it promised -- and splitting them across the
    caller made the order they must be asked in a matter of reading
    control flow rather than of reading one function.

    Returns:
        The transitioned-to-FAILED result, or ``None`` when the run may
        proceed to review.
    """
    run = move.execution_result
    reason = run.termination_reason
    if run.metadata.get(_NO_OP_JUSTIFICATION_KEY):
        # Recording why nothing was produced is the one sanctioned way to
        # finish a run empty-handed, and it answers every question below.
        return None
    task_execution = move.ctx.task_execution
    task_expects_artifacts = task_execution is not None and bool(
        task_execution.task.artifacts_expected
    )
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
    if empty_run_fails and (
        reason == TerminationReason.NO_OP
        or (task_expects_artifacts and run.total_tool_calls == 0)
    ):
        return await _transition_to_failed(move, reason=_EMPTY_RUN_REASON)

    # The tool-call count above is a proxy; this is the question it stands in
    # for. An agent that read files, wrote nothing and stopped passes the
    # proxy, so ask the workspace whether the declared deliverables exist.
    # Deliberately not exempted for a resumed run: the resume exemption exists
    # because this segment's turn count says nothing about earlier segments,
    # and the filesystem has no such blind spot. Whatever an earlier segment
    # produced is still on disk, so a resumed run with none of its declared
    # paths present delivered nothing, whichever segment was supposed to.
    if not task_expects_artifacts:
        return None
    presence = await _absent_artifacts(artifact_probe, move.ctx)
    if presence is not None and presence.nothing_delivered:
        return await _transition_to_failed(
            move,
            reason=_MISSING_ARTIFACTS_REASON.format(paths=", ".join(presence.missing)),
        )
    return None


async def _absent_artifacts(
    artifact_probe: ExpectedArtifactProbe | None,
    ctx: AgentContext,
) -> ArtifactPresence | None:
    """Ask the workspace which declared artifacts are missing.

    Args:
        artifact_probe: The wired probe, or ``None`` when the engine was
            built without a workspace root to resolve against.
        ctx: The finished run's context, carrying the task and its project.

    Returns:
        What the workspace says, or ``None`` when the question could not be
        asked -- no probe, no project, or a probe that raised.

        ``None`` lets review proceed rather than failing the task, because a
        storage fault is not evidence an agent delivered nothing. It is not
        silent: a probe that raised is logged at ERROR, and the same fault
        makes the deliverable reader hand the reviewer an explicit
        unreadable-workspace marker, so the run reaches review carrying the
        fact that it could not be verified rather than looking verified.
    """
    if artifact_probe is None or ctx.task_execution is None:
        return None
    task = ctx.task_execution.task
    project_id = str(task.project)
    if not project_id.strip():
        return None
    try:
        return await artifact_probe(project_id, task.artifacts_expected)
    except OSError as exc:
        reraise_critical(exc)
        logger.error(
            EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
            task_id=str(task.id),
            project_id=project_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


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


async def _transition_to_review(
    move: _Move,
    *,
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
    ctx = move.ctx
    synced = False
    for target, step_reason in _COMPLETION_STEPS:
        try:
            ctx, synced = await transition_and_sync(
                ctx,
                target_status=target,
                reason=step_reason,
                agent_id=move.agent_id,
                task_id=move.task_id,
                task_engine=move.task_engine,
            )
        except (ValueError, ExecutionStateError) as exc:
            logger.warning(
                EXECUTION_ENGINE_ERROR,
                agent_id=move.agent_id,
                task_id=move.task_id,
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
            move.approval_store,
            agent_id=move.agent_id,
            task_id=move.task_id,
            task=ctx.task_execution.task,
            outcome=RunOutcome.SUCCEEDED,
        )
        await _maybe_auto_review(
            review_gate,
            review_pipeline,
            agent_id=move.agent_id,
            task_id=move.task_id,
        )

    if ctx is move.execution_result.context:
        return move.execution_result
    return move.execution_result.model_copy(update={"context": ctx})


async def _transition_to_failed(move: _Move, *, reason: str) -> ExecutionResult:
    """Transition a run that did not deliver IN_PROGRESS -> FAILED, then flag it.

    A work task that produced no artifacts, or a run that stopped without
    finishing, must surface a visible failure with the reason, never a
    silent no-op success pushed to review and never a task left sitting at
    IN_PROGRESS. A FAILED-outcome review approval is created (risk escalated
    one level above the task's stakes, so never LOW) so the failure lands in
    the operator's approval queue as an unmistakable failure rather than
    being invisible.

    Args:
        move: The run being transitioned, and where the failure reports.
        reason: Why the run failed, recorded on the transition and surfaced
            to the operator.

    Returns:
        A copy of the run with the context updated to ``FAILED``.

    Raises:
        ExecutionStateError: When the transition itself fails. Swallowing it
            leaves the task sitting at ``IN_PROGRESS`` behind a warning
            nobody reads, which is the invisibility this whole path exists
            to close: the stall derivation reads ``IN_PROGRESS`` as still
            moving, so the initiative never replans and never completes.
            Raising hands the run to the caller's own error handling, which
            is the only remaining place that can act on it.
    """
    try:
        ctx, synced = await transition_and_sync(
            move.ctx,
            target_status=TaskStatus.FAILED,
            reason=reason,
            agent_id=move.agent_id,
            task_id=move.task_id,
            task_engine=move.task_engine,
            critical=True,
        )
    except (ValueError, ExecutionStateError) as exc:
        logger.error(
            EXECUTION_ENGINE_ERROR,
            agent_id=move.agent_id,
            task_id=move.task_id,
            context="Post-execution FAILED transition failed",
            reason=reason,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = (
            f"Task {move.task_id} did not deliver ({reason}) and could not be"
            f" transitioned to FAILED; it is left at its previous status"
        )
        raise ExecutionStateError(msg) from exc
    # Emit the no-artifacts fail event only after FAILED is persisted, so
    # the record never claims a failure that the transition did not land.
    logger.warning(
        EXECUTION_ENGINE_NO_ARTIFACTS_FAILED,
        agent_id=move.agent_id,
        task_id=move.task_id,
        context="Run did not deliver; task failed",
        reason=reason,
    )
    # Only queue the failure approval once the central engine reflects FAILED:
    # a swallowed/rejected sync leaves the engine's task IN_PROGRESS, and a
    # ``review:task_failed`` item pointing at an in-progress task would let a
    # later decision transition the wrong state.
    if synced and ctx.task_execution is not None:
        await create_review_approval(
            move.approval_store,
            agent_id=move.agent_id,
            task_id=move.task_id,
            task=ctx.task_execution.task,
            outcome=RunOutcome.FAILED,
        )
    return move.execution_result.model_copy(update={"context": ctx})
