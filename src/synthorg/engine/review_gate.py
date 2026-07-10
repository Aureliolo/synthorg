# module-kind: service
"""Review gate service -- IN_REVIEW task transitions on approval decisions.

Handles the post-execution review gate: when a human approves or rejects
a completed task, this service transitions it from IN_REVIEW to COMPLETED
(approve) or IN_PROGRESS (reject/rework) via the TaskEngine.

A FAILED task is decided on the same gate but with distinct transition
targets: approve is an acknowledgement (the task stays FAILED, no task-engine
transition, no completion gate), reject is a rework retry to ASSIGNED (not
IN_PROGRESS). See ``_decide_failed_task``.

Enforces structural no-self-review at the approval gate boundary:
the decider must not be the same agent as the task's original executor.
Every decision is appended to the auditable decisions drop-box.

The preflight ``check_can_decide`` method lets the API controller run
the self-review check and task lookup *before* persisting the approval
decision, so a self-review attempt never leaves a decided approval row
or a broadcast WebSocket event behind.
"""

import asyncio
from typing import TYPE_CHECKING, Literal

from synthorg.core.actor_context import resolve_decided_by
from synthorg.core.task import Task
from synthorg.core.task_enums import Stakes, TaskStatus, compare_stakes
from synthorg.engine._review_completion_gates import (
    map_pipeline_verdict,
    run_completion_gates,
)
from synthorg.engine._review_gate_drain import await_shielded_drain
from synthorg.engine._review_gate_receipt import DeliverableReceiptSeam, emit_receipt
from synthorg.engine._review_gate_record import ReviewGateRecordMixin
from synthorg.engine._review_gate_wiring import ReviewGateWiringMixin
from synthorg.engine.errors import SelfReviewError, TaskNotFoundError
from synthorg.engine.review.models import PipelineResult
from synthorg.engine.review.pipeline import ReviewPipeline
from synthorg.engine.review_gate_inputs import DeliverableReviewInputBuilder
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.background_tasks import BackgroundTaskRegistry
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_REVIEW_ACKNOWLEDGED,
    APPROVAL_GATE_REVIEW_COMPLETED,
    APPROVAL_GATE_REVIEW_REWORK,
    APPROVAL_GATE_REVIEW_TRANSITION_FAILED,
    APPROVAL_GATE_TASK_NOT_FOUND,
    APPROVAL_GATE_TASK_UNASSIGNED,
)
from synthorg.observability.events.red_team import RED_TEAM_GATE_DISPATCHED
from synthorg.observability.events.security import (
    SECURITY_APPROVAL_SELF_REVIEW_PREVENTED,
)
from synthorg.security.redteam.protocol import RedTeamGate
from synthorg.security.visionverify.models import VisionReviewInput
from synthorg.security.visionverify.protocol import VisionVerifierGate

if TYPE_CHECKING:
    from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)


class ReviewGateService(ReviewGateWiringMixin, ReviewGateRecordMixin):
    """Handles IN_REVIEW -> COMPLETED/IN_PROGRESS transitions.

    Called by the approval controller when a review-gate approval
    is approved or rejected.  Enforces no-self-review (the decider
    must not be the original executing agent) and records every
    decision to the decisions drop-box (best effort).

    Args:
        task_engine: Centralized task engine for status sync and
            task lookup (required for self-review enforcement).
        persistence: Optional persistence backend -- ``decision_records``
            is accessed lazily so the backend may be constructed before
            ``persistence.connect()`` is called.  When ``None``, the
            preflight and state-transition paths still run (they only
            need ``task_engine``); decision recording degrades to a
            WARNING-level no-op so the self-review / missing-task
            fail-fast guarantee still holds in backends that do not
            have a persistence layer wired up.
    """

    def __init__(  # noqa: PLR0913 -- boot-wired collaborators, all optional
        self,
        *,
        task_engine: TaskEngine,
        persistence: PersistenceBackend | None = None,
        red_team_gate: RedTeamGate | None = None,
        red_team_input_builder: DeliverableReviewInputBuilder | None = None,
        red_team_on_missing_deliverable: Literal["block", "skip"] = "block",
        red_team_min_stakes: Stakes = Stakes.HIGH,
        vision_gate: VisionVerifierGate | None = None,
        receipt_service: DeliverableReceiptSeam | None = None,
        background_tasks: BackgroundTaskRegistry | None = None,
    ) -> None:
        self._task_engine = task_engine
        self._persistence = persistence
        self._red_team_gate = red_team_gate
        self._red_team_input_builder = red_team_input_builder
        self._red_team_on_missing_deliverable: Literal["block", "skip"] = (
            red_team_on_missing_deliverable
        )
        self._red_team_min_stakes = red_team_min_stakes
        self._vision_gate = vision_gate
        self._receipt_service = receipt_service
        self._background_tasks = background_tasks

    async def check_can_decide(
        self,
        *,
        task_id: str,
        decided_by: str,
    ) -> Task:
        """Preflight check: task exists and decider is not the executor.

        Call this BEFORE persisting the approval decision so that a
        rejected preflight never leaves a decided approval row behind.

        Args:
            task_id: The task identifier.
            decided_by: The resolved decider identity. Callers resolve
                the bound actor (ADR-0003) via :func:`resolve_decided_by`
                before invoking the gate.

        Returns:
            The validated ``Task`` fetched from the engine.  Returned
            for callers that want to inspect task metadata (status,
            assignee) right after the preflight; ``complete_review``
            independently re-fetches the task as defense-in-depth.

        Raises:
            TaskNotFoundError: If the task cannot be found.
            SelfReviewError: If the decider is the task's original
                executing agent.
        """
        task = await self._task_engine.get_task(task_id)
        if task is None:
            logger.warning(
                APPROVAL_GATE_TASK_NOT_FOUND,
                task_id=task_id,
                decided_by=decided_by,
            )
            msg = f"Task {task_id!r} not found during review gate preflight"
            raise TaskNotFoundError(msg)

        self._check_self_review(task, decided_by=decided_by)
        return task

    async def complete_review(
        self,
        *,
        task_id: str,
        approved: bool,
        decided_by: str,
        reason: str | None = None,
        approval_id: str | None = None,
    ) -> None:
        """Transition a task out of IN_REVIEW and record the decision.

        On approve: IN_REVIEW -> COMPLETED.
        On reject: IN_REVIEW -> IN_PROGRESS (rework).
        Self-review check runs again as defense in depth.

        ``decided_by`` is the already-resolved deciding actor; the sole
        caller (:meth:`dispatch_completion`) resolves it via
        :func:`resolve_decided_by` before the work is optionally
        backgrounded, so the actor is captured while the request context
        is still live (ADR-0003).

        Raises:
            TaskNotFoundError: If the task cannot be found.
            SelfReviewError: If the decider is the task executor.
        """
        task = await self.check_can_decide(task_id=task_id, decided_by=decided_by)

        # Normalize the reason once at the service boundary: empty or
        # whitespace-only strings collapse to None so the task transition
        # history and DecisionRecord.reason carry the identical canonical
        # value (a single normalisation site keeps them from diverging).
        normalized_reason = reason.strip() if reason and reason.strip() else None

        # A failed run's review is not an accept/reject of finished work: an
        # approve acknowledges the failure (the task stays FAILED, no phantom
        # COMPLETED), a reject requests a retry (FAILED -> ASSIGNED). The
        # adversarial completion gates never run on a failure.
        if task.status == TaskStatus.FAILED:
            await self._decide_failed_task(
                task=task,
                approved=approved,
                decided_by=decided_by,
                normalized_reason=normalized_reason,
                approval_id=approval_id,
            )
            return

        (
            target,
            transition_reason,
            event,
            approved,
            normalized_reason,
        ) = await self._resolve_review_target(
            task=task,
            approved=approved,
            decided_by=decided_by,
            normalized_reason=normalized_reason,
        )

        await self._apply_decision(
            task=task,
            target=target,
            transition_reason=transition_reason,
            event=event,
            decided_by=decided_by,
            approved=approved,
            approval_id=approval_id,
            normalized_reason=normalized_reason,
        )

    async def _resolve_review_target(
        self,
        *,
        task: Task,
        approved: bool,
        decided_by: str,
        normalized_reason: str | None,
    ) -> tuple[TaskStatus, str, str, bool, str | None]:
        """Resolve the target status + audit fields for an IN_REVIEW decision.

        On reject, the target is a straight rework to IN_PROGRESS. On approve,
        the configured adversarial gate(s) get the last word before COMPLETED:
        a BLOCK reroutes the human-approved task back to IN_PROGRESS as rework
        (this is what makes the red-team gate fire on the real approval path,
        not only ``run_pipeline``) and rewrites ``transition_reason`` with its
        block summary; the recorded decision reason is realigned to match so
        the audit row carries the block reason, not the stale human note (as
        the pipeline path already does via ``normalized_reason``).

        Returns:
            ``(target, transition_reason, event, approved, normalized_reason)``.
        """
        if not approved:
            transition_reason = f"Review rejected by {decided_by}"
            if normalized_reason is not None:
                transition_reason += f": {normalized_reason}"
            return (
                TaskStatus.IN_PROGRESS,
                transition_reason,
                APPROVAL_GATE_REVIEW_REWORK,
                approved,
                normalized_reason,
            )

        target = TaskStatus.COMPLETED
        transition_reason = f"Review approved by {decided_by}"
        if normalized_reason is not None:
            transition_reason += f": {normalized_reason}"
        event = APPROVAL_GATE_REVIEW_COMPLETED
        target, transition_reason, event, approved = await run_completion_gates(
            red_team_gate=self._red_team_gate,
            vision_gate=self._vision_gate,
            red_team_input_builder=self._red_team_input_builder,
            on_missing_deliverable=self._red_team_on_missing_deliverable,
            task=task,
            target=target,
            transition_reason=transition_reason,
            event=event,
            approved=approved,
            vision_input=None,
            red_team_min_stakes=self._red_team_min_stakes,
        )
        if not approved:
            normalized_reason = transition_reason
        return target, transition_reason, event, approved, normalized_reason

    async def dispatch_completion(
        self,
        *,
        task_id: str,
        approved: bool,
        decided_by: str | None = None,
        reason: str | None = None,
        approval_id: str | None = None,
    ) -> bool:
        """Run :meth:`complete_review`, backgrounding a gated approval.

        When the approval would run the inline red-team gate (an approve
        with a configured gate and a background registry) the completion
        is spawned as a tracked background task so the approve/reject HTTP
        response is not blocked by the inline AgentEngine evaluation,
        mirroring the mid-execution resume dispatch. The task holds in
        IN_REVIEW until the background gate transitions it. Every other
        case (reject, no gate, or no registry) runs inline.

        Returns:
            ``True`` when the completion was dispatched to the background
            (the caller must not expect a synchronous transition or its
            engine-layer errors); ``False`` when it ran inline.
        """
        # Resolve the actor here, in the caller's context: a backgrounded
        # complete_review runs outside the request's bound actor, so a late
        # resolve would lose it, and the dispatch log / failure-event
        # metadata would otherwise record ``decided_by=None``. The inner
        # resolve in complete_review is idempotent on the concrete string.
        decided_by = resolve_decided_by(decided_by)
        gated = (
            approved
            and self._red_team_gate is not None
            and self._background_tasks is not None
        )
        if gated:
            # The inline red-team evaluation only runs when the task's stakes
            # meet the configured red_team_min_stakes; a below-threshold
            # approve skips the gate, so run it inline for an immediate
            # transition rather than deferring a no-op to the background. A
            # missing task also runs inline so complete_review re-fetches and
            # raises TaskNotFoundError synchronously (the caller maps it to a
            # 404) instead of swallowing it in a background task.
            task = await self._task_engine.get_task(task_id)
            # A failed-task review never runs the red-team completion gate
            # (it acknowledges/retries, not accepts work), so run it inline
            # rather than deferring a no-op to the background.
            if (
                task is None
                or task.status == TaskStatus.FAILED
                or compare_stakes(task.stakes, self._red_team_min_stakes) < 0
            ):
                gated = False
        if gated and self._background_tasks is not None:
            logger.info(
                RED_TEAM_GATE_DISPATCHED,
                task_id=task_id,
                decided_by=decided_by,
                approval_id=approval_id,
            )
            _ = self._background_tasks.spawn(
                self.complete_review(
                    task_id=task_id,
                    approved=approved,
                    decided_by=decided_by,
                    reason=reason,
                    approval_id=approval_id,
                ),
                event=APPROVAL_GATE_REVIEW_TRANSITION_FAILED,
                task_id=task_id,
                decided_by=decided_by,
                approval_id=approval_id,
            )
            return True
        await self.complete_review(
            task_id=task_id,
            approved=approved,
            decided_by=decided_by,
            reason=reason,
            approval_id=approval_id,
        )
        return False

    async def run_pipeline(
        self,
        *,
        task_id: str,
        pipeline: ReviewPipeline,
        decided_by: str,
        approval_id: str | None = None,
        vision_input: VisionReviewInput | None = None,
    ) -> PipelineResult:
        """Drive a review pipeline and apply its final verdict.

        This is the pipeline-driven entry point. It runs the
        self-review preflight, executes every stage in ``pipeline``,
        maps the aggregated verdict to the same task-engine transition
        and decision-record flow used by :meth:`complete_review`, runs
        the shared adversarial completion-gate chain, and returns the
        :class:`PipelineResult` so callers can surface stage details to
        the UI.

        Args:
            task_id: The task under review.
            pipeline: Review pipeline to execute.
            decided_by: Identity attributed to the decision (the
                pipeline's operator or the invoking agent).
            approval_id: Optional foreign key to an approval item.
            vision_input: Optional GUI-deliverable review payload
                (screenshots + brief). When this and ``vision_gate`` are
                both wired, a still-approved verdict is offered to the
                vision gate after the red-team gate; a BLOCK verdict
                routes the task back to IN_PROGRESS as rework. Absent
                input SKIPS the vision gate (non-GUI deliverable). The
                red-team review input is built from the task's recorded
                deliverable inside the shared gate chain, not passed in.

        Returns:
            The :class:`PipelineResult` produced by the pipeline
            (irrespective of the final verdict).

        Raises:
            TaskNotFoundError: If the task cannot be found.
            SelfReviewError: If the decider is the task's executor.
        """
        task = await self.check_can_decide(
            task_id=task_id,
            decided_by=decided_by,
        )
        result = await pipeline.run(task)
        target, transition_reason, event, approved = map_pipeline_verdict(
            result, decided_by
        )
        (
            target,
            transition_reason,
            event,
            approved,
        ) = await run_completion_gates(
            red_team_gate=self._red_team_gate,
            vision_gate=self._vision_gate,
            red_team_input_builder=self._red_team_input_builder,
            on_missing_deliverable=self._red_team_on_missing_deliverable,
            task=task,
            target=target,
            transition_reason=transition_reason,
            event=event,
            approved=approved,
            vision_input=vision_input,
            red_team_min_stakes=self._red_team_min_stakes,
        )
        await self._apply_decision(
            task=task,
            target=target,
            transition_reason=transition_reason,
            event=event,
            decided_by=decided_by,
            approved=approved,
            approval_id=approval_id,
            normalized_reason=transition_reason,
        )
        return result

    async def _decide_failed_task(
        self,
        *,
        task: Task,
        approved: bool,
        decided_by: str,
        normalized_reason: str | None,
        approval_id: str | None,
    ) -> None:
        """Decide a failed-run review: approve acknowledges, reject retries.

        Approve leaves the task FAILED and records the acknowledgement (no
        task-engine transition, so a failure is never laundered into
        COMPLETED). Reject requests a retry via the sole valid exit from
        FAILED (``ASSIGNED``), reusing the shared decision-apply path.
        """
        if approved:
            reason_text = f"Failure acknowledged by {decided_by}"
            if normalized_reason is not None:
                reason_text += f": {normalized_reason}"
            logger.info(
                APPROVAL_GATE_REVIEW_ACKNOWLEDGED,
                task_id=str(task.id),
                decided_by=decided_by,
                approval_id=approval_id,
                status=task.status.value,
            )
            # An acknowledgement records no transition, but the audit write
            # must still survive a shutdown-drain cancellation exactly as the
            # transition path's does, so a failure is never left unrecorded.
            await await_shielded_drain(
                asyncio.create_task(
                    self._record_decision(
                        task=task,
                        decided_by=decided_by,
                        approved=True,
                        reason=reason_text,
                        approval_id=approval_id,
                    )
                ),
                task_id=str(task.id),
                approval_id=approval_id,
                decided_by=decided_by,
            )
            return

        transition_reason = f"Rework requested by {decided_by}"
        if normalized_reason is not None:
            transition_reason += f": {normalized_reason}"
        await self._apply_decision(
            task=task,
            target=TaskStatus.ASSIGNED,
            transition_reason=transition_reason,
            event=APPROVAL_GATE_REVIEW_REWORK,
            decided_by=decided_by,
            approved=False,
            approval_id=approval_id,
            normalized_reason=normalized_reason,
        )

    async def _apply_decision(  # noqa: PLR0913
        self,
        *,
        task: Task,
        target: TaskStatus,
        transition_reason: str,
        event: str,
        decided_by: str,
        approved: bool,
        approval_id: str | None,
        normalized_reason: str | None,
    ) -> None:
        """Run the transition + log + decision-record side effects.

        Shared by :meth:`complete_review` (human-driven) and
        :meth:`run_pipeline` (pipeline-driven). The strict
        :meth:`TaskEngine.transition_task` commits the task-state change
        first and raises a typed error on a rejected mutation (invalid
        transition, version conflict, task vanished) so the decision is
        never recorded against a transition that did not land. The audit
        log, decision record, and deliverable receipt are emitted only
        after the commit so an observability or persistence failure in
        those steps never rolls back a completed transition.

        Raises:
            TaskNotFoundError: The task vanished before the transition.
            TaskVersionConflictError: A concurrent modification was
                detected.
            TaskMutationError: The transition was otherwise rejected
                (e.g. not a legal edge from the current status).
            TaskEngineError: Any other ``transition_task`` failure
                (engine not running, queue full, internal error) is
                logged and re-raised, not swallowed, so the caller
                surfaces a real status code instead of a phantom 200.
            CancelledError: Re-raised after the shielded decision-record +
                receipt work has run to completion, so a shutdown-drain
                cancellation propagates without losing those side effects.
        """
        await self._transition_or_raise(
            task=task,
            target=target,
            transition_reason=transition_reason,
            decided_by=decided_by,
            approval_id=approval_id,
        )

        logger.info(
            event,
            task_id=str(task.id),
            decided_by=decided_by,
            approval_id=approval_id,
            target_status=target.value,
        )

        # The transition has committed. The audit write and receipt must run
        # to completion as ONE unit even if a shutdown-drain cancels the
        # (possibly backgrounded) completion task, so a COMPLETED task is
        # never left without a decision record.
        async def _record_and_emit() -> None:
            await self._record_decision(
                task=task,
                decided_by=decided_by,
                approved=approved,
                reason=normalized_reason,
                approval_id=approval_id,
            )
            await emit_receipt(self._receipt_service, task, target)

        await await_shielded_drain(
            asyncio.create_task(_record_and_emit()),
            task_id=str(task.id),
            approval_id=approval_id,
            decided_by=decided_by,
        )

    async def _transition_or_raise(
        self,
        *,
        task: Task,
        target: TaskStatus,
        transition_reason: str,
        decided_by: str,
        approval_id: str | None,
    ) -> None:
        """Commit the IN_REVIEW decision's task-state transition.

        A rejected mutation raises here rather than being swallowed as a
        best-effort sync, so the decision is NOT recorded and the caller
        surfaces a real status code instead of a phantom 200.

        Raises:
            TaskEngineError: Any ``transition_task`` failure, logged and
                re-raised (never swallowed).
        """
        try:
            await self._task_engine.transition_task(
                str(task.id),
                target,
                requested_by="review-gate-service",
                reason=transition_reason,
            )
        except Exception as exc:
            logger.warning(
                APPROVAL_GATE_REVIEW_TRANSITION_FAILED,
                task_id=str(task.id),
                decided_by=decided_by,
                approval_id=approval_id,
                target_status=target.value,
                stage="transition_task",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    def _check_self_review(self, task: Task, *, decided_by: str) -> None:
        """Raise ``SelfReviewError`` when the decider is the executor.

        If ``task.assigned_to`` is ``None`` the check is skipped and a
        WARNING is logged: a task reaching review without an assignee
        is an anomalous state worth operator attention.

        Raises:
            SelfReviewError: When ``decided_by`` matches the task's
                assigned executor (review-by-self prevented).
        """
        if task.assigned_to is None:
            logger.warning(
                APPROVAL_GATE_TASK_UNASSIGNED,
                task_id=str(task.id),
                decided_by=decided_by,
                status=task.status.value,
            )
            return
        if decided_by == task.assigned_to:
            logger.warning(
                SECURITY_APPROVAL_SELF_REVIEW_PREVENTED,
                task_id=str(task.id),
                agent_id=decided_by,
            )
            raise SelfReviewError(task_id=str(task.id), agent_id=decided_by)
