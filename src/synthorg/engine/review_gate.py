"""Review gate service -- IN_REVIEW task transitions on approval decisions.

Handles the post-execution review gate: when a human approves or rejects
a completed task, this service transitions it from IN_REVIEW to COMPLETED
(approve) or IN_PROGRESS (reject/rework) via the TaskEngine.

Enforces structural no-self-review at the approval gate boundary:
the decider must not be the same agent as the task's original executor.
Every decision is appended to the auditable decisions drop-box.

The preflight ``check_can_decide`` method lets the API controller run
the self-review check and task lookup *before* persisting the approval
decision, so a self-review attempt never leaves a decided approval row
or a broadcast WebSocket event behind.
"""

from typing import TYPE_CHECKING, Literal

from synthorg.core.actor_context import resolve_decided_by
from synthorg.core.enums import TaskStatus
from synthorg.engine._review_completion_gates import (
    map_pipeline_verdict,
    run_completion_gates,
)
from synthorg.engine._review_gate_receipt import DeliverableReceiptSeam, emit_receipt
from synthorg.engine._review_gate_record import ReviewGateRecordMixin
from synthorg.engine._review_gate_wiring import ReviewGateWiringMixin
from synthorg.engine.errors import SelfReviewError, TaskNotFoundError
from synthorg.engine.review.models import PipelineResult
from synthorg.engine.task_sync import sync_to_task_engine
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
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

if TYPE_CHECKING:
    from synthorg.core.task import Task
    from synthorg.engine.review.pipeline import ReviewPipeline
    from synthorg.engine.review_gate_inputs import DeliverableReviewInputBuilder
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.observability.background_tasks import BackgroundTaskRegistry
    from synthorg.persistence.protocol import PersistenceBackend
    from synthorg.security.redteam.protocol import RedTeamGate
    from synthorg.security.visionverify.models import VisionReviewInput
    from synthorg.security.visionverify.protocol import VisionVerifierGate

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
        self._vision_gate = vision_gate
        self._receipt_service = receipt_service
        self._background_tasks = background_tasks

    async def check_can_decide(
        self,
        *,
        task_id: str,
        decided_by: str | None = None,
    ) -> Task:
        """Preflight check: task exists and decider is not the executor.

        Call this BEFORE persisting the approval decision so that a
        rejected preflight never leaves a decided approval row behind.

        Args:
            task_id: The task identifier.
            decided_by: Optional explicit decider override (system /
                non-HTTP paths). When omitted the bound actor (RFC#3 /
                ADR-0003) supplies it via :func:`resolve_decided_by`.

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
        decided_by = resolve_decided_by(decided_by)
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

    async def complete_review(  # noqa: PLR0913
        self,
        *,
        task_id: str,
        requested_by: str,
        approved: bool,
        decided_by: str | None = None,
        reason: str | None = None,
        approval_id: str | None = None,
    ) -> None:
        """Transition a task out of IN_REVIEW and record the decision.

        On approve: IN_REVIEW -> COMPLETED.
        On reject: IN_REVIEW -> IN_PROGRESS (rework).
        Self-review check runs again as defense in depth.

        ``decided_by`` is an optional explicit override (system /
        non-HTTP paths); when omitted the bound actor supplies it via
        :func:`resolve_decided_by` (RFC#3 / ADR-0003).

        Raises:
            TaskNotFoundError: If the task cannot be found.
            SelfReviewError: If the decider is the task executor.
        """
        decided_by = resolve_decided_by(decided_by)
        task = await self.check_can_decide(task_id=task_id, decided_by=decided_by)

        # Normalize the reason once at the service boundary: empty or
        # whitespace-only strings collapse to None so the task
        # transition history and DecisionRecord.reason both carry the
        # same canonical value.  ``_record_decision`` previously
        # re-normalized, which allowed the transition reason and the
        # audit record to drift (e.g. "Review rejected by bob:   ").
        normalized_reason = reason.strip() if reason and reason.strip() else None

        if approved:
            target = TaskStatus.COMPLETED
            transition_reason = f"Review approved by {decided_by}"
            event = APPROVAL_GATE_REVIEW_COMPLETED
            # The configured adversarial gate(s) get the last word before
            # COMPLETED: a BLOCK reroutes the human-approved task back to
            # IN_PROGRESS as rework. This is what makes the red-team gate
            # fire on the real approval path, not only run_pipeline.
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
                vision_input=None,
            )
            # A gate that reroutes the human-approved task back to rework
            # rewrites ``transition_reason`` with its block summary; align
            # the recorded decision reason so the audit row matches the
            # transition instead of carrying the stale human note (which
            # the pipeline path already does via normalized_reason=
            # transition_reason).
            if not approved:
                normalized_reason = transition_reason
        else:
            target = TaskStatus.IN_PROGRESS
            transition_reason = f"Review rejected by {decided_by}"
            if normalized_reason is not None:
                transition_reason += f": {normalized_reason}"
            event = APPROVAL_GATE_REVIEW_REWORK

        await self._apply_decision(
            task=task,
            target=target,
            transition_reason=transition_reason,
            event=event,
            decided_by=decided_by,
            requested_by=requested_by,
            approved=approved,
            approval_id=approval_id,
            normalized_reason=normalized_reason,
        )

    async def dispatch_completion(  # noqa: PLR0913 -- mirrors complete_review
        self,
        *,
        task_id: str,
        requested_by: str,
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
        decided_by = resolve_decided_by(decided_by)
        if (
            approved
            and self._red_team_gate is not None
            and self._background_tasks is not None
        ):
            logger.info(
                RED_TEAM_GATE_DISPATCHED,
                task_id=task_id,
                decided_by=decided_by,
                approval_id=approval_id,
            )
            self._background_tasks.spawn(
                self.complete_review(
                    task_id=task_id,
                    requested_by=requested_by,
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
            requested_by=requested_by,
            approved=approved,
            decided_by=decided_by,
            reason=reason,
            approval_id=approval_id,
        )
        return False

    async def run_pipeline(  # noqa: PLR0913
        self,
        *,
        task_id: str,
        pipeline: ReviewPipeline,
        decided_by: str,
        requested_by: str,
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
            requested_by: Agent that requested the review.
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
        )
        await self._apply_decision(
            task=task,
            target=target,
            transition_reason=transition_reason,
            event=event,
            decided_by=decided_by,
            requested_by=requested_by,
            approved=approved,
            approval_id=approval_id,
            normalized_reason=transition_reason,
        )
        return result

    async def _apply_decision(  # noqa: PLR0913
        self,
        *,
        task: Task,
        target: TaskStatus,
        transition_reason: str,
        event: str,
        decided_by: str,
        requested_by: str,
        approved: bool,
        approval_id: str | None,
        normalized_reason: str | None,
    ) -> None:
        """Run the transition + log + decision-record side effects.

        Shared by :meth:`complete_review` (human-driven) and
        :meth:`run_pipeline` (pipeline-driven). Behavior is
        preserved byte-for-byte: ``sync_to_task_engine`` runs
        first, then the audit log entry, then the drop-box
        append.
        """
        try:
            await sync_to_task_engine(
                self._task_engine,
                target_status=target,
                task_id=task.id,
                agent_id="review-gate-service",
                reason=transition_reason,
            )
        except Exception as exc:
            logger.warning(
                APPROVAL_GATE_REVIEW_TRANSITION_FAILED,
                task_id=task.id,
                decided_by=decided_by,
                target_status=target.value,
                stage="sync_to_task_engine",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

        logger.info(
            event,
            task_id=task.id,
            requested_by=requested_by,
            decided_by=decided_by,
            target_status=target.value,
        )

        await self._record_decision(
            task=task,
            decided_by=decided_by,
            approved=approved,
            reason=normalized_reason,
            approval_id=approval_id,
        )

        await emit_receipt(self._receipt_service, task, target)

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
                task_id=task.id,
                decided_by=decided_by,
                status=task.status.value,
            )
            return
        if decided_by == task.assigned_to:
            logger.warning(
                SECURITY_APPROVAL_SELF_REVIEW_PREVENTED,
                task_id=task.id,
                agent_id=decided_by,
            )
            raise SelfReviewError(task_id=task.id, agent_id=decided_by)
