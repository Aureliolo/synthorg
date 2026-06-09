# module-kind: code
"""Completion-gate chain for the review gate.

Houses the adversarial-gate chain (red-team then vision) the review
gate runs before an IN_REVIEW -> COMPLETED transition, plus the
pipeline-verdict mapping. Both completion entry points share it: the
pipeline-driven ``run_pipeline`` and the human-driven ``complete_review``
call :func:`run_completion_gates`, so a configured gate fires on every
path to COMPLETED rather than only the pipeline one.

Extracted from ``review_gate.py`` so that module stays within its size
budget and the gate-application logic is unit-testable without the full
service. Each gate returns the (possibly rerouted) transition tuple
``(target, reason, event, approved)``.
"""

from typing import TYPE_CHECKING, Literal

from synthorg.core.task import Task
from synthorg.core.task_enums import Stakes, TaskStatus, compare_stakes
from synthorg.engine.review.models import PipelineResult, ReviewVerdict
from synthorg.engine.review_gate_inputs import DeliverableReviewInputBuilder
from synthorg.observability import get_logger
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_REVIEW_COMPLETED,
    APPROVAL_GATE_REVIEW_REWORK,
)
from synthorg.observability.events.red_team import (
    RED_TEAM_GATE_SKIPPED,
    RED_TEAM_NO_DELIVERABLE,
    RED_TEAM_REWORK_ROUTED,
)
from synthorg.observability.events.review_pipeline import (
    APPROVAL_GATE_PIPELINE_ALL_SKIPPED,
)
from synthorg.observability.events.vision_verify import (
    VISION_GATE_SKIPPED,
    VISION_REWORK_ROUTED,
)
from synthorg.security.redteam.protocol import RedTeamGate
from synthorg.security.visionverify.models import VisionReviewInput
from synthorg.security.visionverify.protocol import VisionVerifierGate

if TYPE_CHECKING:
    from synthorg.security.redteam.models import RedTeamReviewInput

logger = get_logger(__name__)

#: Transition tuple a gate returns: (target, reason, event, approved).
GateOutcome = tuple[TaskStatus, str, str, bool]


async def run_completion_gates(  # noqa: PLR0913 -- gate chain inputs, all required
    *,
    red_team_gate: RedTeamGate | None,
    vision_gate: VisionVerifierGate | None,
    red_team_input_builder: DeliverableReviewInputBuilder | None,
    on_missing_deliverable: Literal["block", "skip"],
    task: Task,
    target: TaskStatus,
    transition_reason: str,
    event: str,
    approved: bool,
    vision_input: VisionReviewInput | None,
    red_team_min_stakes: Stakes,
) -> GateOutcome:
    """Run the red-team then vision gates before a COMPLETED transition.

    When the incoming verdict is already a rejection (``approved`` is
    ``False``), returns immediately without evaluating any gate. The
    red-team gate is active only when BOTH the gate and its
    ``red_team_input_builder`` are wired AND the task's stakes are at or
    above ``red_team_min_stakes``: the adversarial pass is reserved for
    consequential work (matching the routing layer, which only marks
    ``red_team_required`` at that same threshold), so low-stakes
    deliverables are not gated. A gate attached without a builder (for
    example a boot with no persistence, where the flight-recorder
    deliverable source is absent) is left inert rather than fail-closed,
    since blocking every completion on a wiring gap the operator did not
    ask for would be worse than not gating. When active, the input is
    built from the task's recorded deliverable; a ``None`` build result is
    handled by :func:`apply_red_team_gate` under ``on_missing_deliverable``.
    A BLOCK from either gate reroutes the task to IN_PROGRESS rework.

    Returns:
        The (possibly rerouted) ``(target, reason, event, approved)``
        tuple. Unchanged when no gate is configured, the task is below
        the stakes threshold, or every gate passes; rerouted to
        IN_PROGRESS rework on any BLOCK.
    """
    if not approved:
        return target, transition_reason, event, approved
    if red_team_gate is not None and red_team_input_builder is not None:
        if compare_stakes(task.stakes, red_team_min_stakes) < 0:
            logger.info(
                RED_TEAM_GATE_SKIPPED,
                task_id=str(task.id),
                reason="below_stakes_threshold",
                stakes=task.stakes.value,
                min_stakes=red_team_min_stakes.value,
                note=(
                    "Red-team gate is wired but the task's stakes are below "
                    "the configured red_team_min_stakes threshold; the "
                    "adversarial review is reserved for higher-stakes work."
                ),
            )
        else:
            red_team_input = await red_team_input_builder.build(task)
            target, transition_reason, event, approved = await apply_red_team_gate(
                gate=red_team_gate,
                on_missing_deliverable=on_missing_deliverable,
                task_id=str(task.id),
                target=target,
                transition_reason=transition_reason,
                event=event,
                approved=approved,
                red_team_input=red_team_input,
            )
    elif red_team_gate is not None:
        logger.warning(
            RED_TEAM_GATE_SKIPPED,
            task_id=str(task.id),
            reason="input_builder_not_wired",
            note=(
                "Red-team gate is attached but no input builder is wired "
                "(e.g. persistence absent); gate is inert this run."
            ),
        )
    if approved:
        target, transition_reason, event, approved = await apply_vision_gate(
            gate=vision_gate,
            task_id=str(task.id),
            target=target,
            transition_reason=transition_reason,
            event=event,
            approved=approved,
            vision_input=vision_input,
        )
    return target, transition_reason, event, approved


async def apply_red_team_gate(  # noqa: PLR0913 -- gate inputs, all required
    *,
    gate: RedTeamGate | None,
    on_missing_deliverable: Literal["block", "skip"],
    task_id: str,
    target: TaskStatus,
    transition_reason: str,
    event: str,
    approved: bool,
    red_team_input: RedTeamReviewInput | None,
) -> GateOutcome:
    """Invoke the red-team gate; override target on BLOCK or missing input.

    When the gate is configured AND a deliverable was built, the gate
    evaluates it; a BLOCK reroutes the task to IN_PROGRESS rework with
    the red-team summary as the reason. PASS / PASS_WITH_FINDINGS leaves
    the target unchanged.

    When the gate is configured but no deliverable could be built, the
    ``on_missing_deliverable`` posture decides: ``"block"`` reroutes to
    IN_PROGRESS (fail-closed; a configured security gate must not pass a
    deliverable it could not inspect), ``"skip"`` leaves the target
    unchanged.

    Returns:
        The (possibly rerouted) ``(target, reason, event, approved)``.
    """
    if gate is None:
        return target, transition_reason, event, approved
    if red_team_input is None:
        if on_missing_deliverable == "skip":
            logger.warning(
                RED_TEAM_GATE_SKIPPED,
                task_id=task_id,
                reason="no_deliverable_skip",
                note=(
                    "Red-team gate is configured but no reviewable deliverable "
                    "was retrievable; skipping per on_missing_deliverable=skip."
                ),
            )
            return target, transition_reason, event, approved
        logger.warning(
            RED_TEAM_NO_DELIVERABLE,
            task_id=task_id,
            reason="no_deliverable_block",
            note=(
                "Red-team gate is configured but no reviewable deliverable was "
                "retrievable; blocking completion (fail-closed) per "
                "on_missing_deliverable=block."
            ),
        )
        return (
            TaskStatus.IN_PROGRESS,
            "Red-team review could not retrieve a deliverable to inspect.",
            APPROVAL_GATE_REVIEW_REWORK,
            False,
        )

    from synthorg.security.redteam.models import RedTeamVerdict  # noqa: PLC0415

    result = await gate.evaluate(red_team_input)
    if result.verdict is not RedTeamVerdict.BLOCK:
        return target, transition_reason, event, approved
    logger.warning(
        RED_TEAM_REWORK_ROUTED,
        task_id=task_id,
        execution_id=red_team_input.execution_id,
        findings=len(result.report.findings),
        verdict=result.verdict.value,
    )
    rework_reason = f"Red-team review blocked completion: {result.report.summary}"
    return (
        TaskStatus.IN_PROGRESS,
        rework_reason,
        APPROVAL_GATE_REVIEW_REWORK,
        False,
    )


async def apply_vision_gate(  # noqa: PLR0913 -- gate inputs, all required
    *,
    gate: VisionVerifierGate | None,
    task_id: str,
    target: TaskStatus,
    transition_reason: str,
    event: str,
    approved: bool,
    vision_input: VisionReviewInput | None,
) -> GateOutcome:
    """Invoke the vision gate; override target on a BLOCK verdict.

    Chained after the red-team gate. The vision gate applies only to GUI
    deliverables, signalled by the caller supplying ``vision_input``.
    When configured but no ``vision_input`` is provided the gate SKIPS
    (unlike the red-team gate it must not fail closed, since most
    deliverables are not GUI apps). A BLOCK reroutes the task to
    IN_PROGRESS rework.

    Returns:
        The (possibly rerouted) ``(target, reason, event, approved)``.
    """
    if gate is None:
        return target, transition_reason, event, approved
    if vision_input is None:
        logger.debug(
            VISION_GATE_SKIPPED,
            task_id=task_id,
            reason="no_vision_input",
            note=(
                "Vision gate is configured but the deliverable carried no "
                "screenshots; skipping (non-GUI deliverable)."
            ),
        )
        return target, transition_reason, event, approved

    from synthorg.security.visionverify.models import VisionVerdict  # noqa: PLC0415

    result = await gate.evaluate(vision_input)
    if result.verdict is not VisionVerdict.BLOCK:
        return target, transition_reason, event, approved
    logger.warning(
        VISION_REWORK_ROUTED,
        task_id=task_id,
        execution_id=vision_input.execution_id,
        findings=len(result.report.findings),
        verdict=result.verdict.value,
    )
    rework_reason = f"Vision review blocked completion: {result.report.summary}"
    return (
        TaskStatus.IN_PROGRESS,
        rework_reason,
        APPROVAL_GATE_REVIEW_REWORK,
        False,
    )


def map_pipeline_verdict(
    result: PipelineResult,
    decided_by: str,
) -> GateOutcome:
    """Translate a pipeline result into the transition inputs.

    Returns:
        ``(target_status, reason, event, approved)`` -- rework tuple on
        FAIL, completed tuple on PASS / SKIP.
    """
    if result.final_verdict is ReviewVerdict.FAIL:
        failing = next(
            (
                stage
                for stage in result.stage_results
                if stage.verdict is ReviewVerdict.FAIL
            ),
            None,
        )
        detail = (
            failing.reason
            if failing and failing.reason
            else "pipeline reported failure"
        )
        return (
            TaskStatus.IN_PROGRESS,
            f"Pipeline rejected review by {decided_by}: {detail}",
            APPROVAL_GATE_REVIEW_REWORK,
            False,
        )
    if result.final_verdict is ReviewVerdict.SKIP:
        logger.warning(
            APPROVAL_GATE_PIPELINE_ALL_SKIPPED,
            task_id=result.task_id,
            decided_by=decided_by,
        )
        stages = ", ".join(stage.stage_name for stage in result.stage_results)
        reason = f"Pipeline all-skipped ({stages or 'no stages'})"
        return (
            TaskStatus.COMPLETED,
            reason,
            APPROVAL_GATE_REVIEW_COMPLETED,
            True,
        )
    stages = ", ".join(stage.stage_name for stage in result.stage_results)
    reason = (
        f"Pipeline passed ({stages})"
        if stages
        else "Pipeline passed (no stages configured)"
    )
    return (
        TaskStatus.COMPLETED,
        reason,
        APPROVAL_GATE_REVIEW_COMPLETED,
        True,
    )
