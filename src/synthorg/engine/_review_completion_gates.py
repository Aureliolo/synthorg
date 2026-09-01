# module-kind: code
"""Completion-gate chain for the review gate.

Orchestrates the full gate chain the review gate runs before an
IN_REVIEW -> COMPLETED transition, in order: the completion oracle
(build/test then agent-session peer review), then the adversarial red-team
and vision gates, plus the pipeline-verdict mapping. Both completion entry
points share it: the pipeline-driven ``run_pipeline`` and the human-driven
``complete_review`` call :func:`run_completion_gates`, so a configured gate
fires on every path to COMPLETED rather than only the pipeline one.

Separating the gate chain from the service lets the gate-application
logic be unit-tested without constructing the full ``ReviewGateService``.
Each gate returns the (possibly rerouted) transition tuple
``(target, reason, event, approved)``.
"""

from typing import Literal

from synthorg.core.redteam_review_input import RedTeamReviewInput
from synthorg.core.task import Task
from synthorg.core.task_enums import (
    Stakes,
    TaskStatus,
)
from synthorg.engine._review_oracle_gates import (
    GateOutcome,
    apply_build_test_gate,
    observe_output_policy,
)
from synthorg.engine._review_oracle_stage import (
    OracleStageConfig,
    apply_oracle_review_stage,
)
from synthorg.engine._review_red_team_gates import (
    RedTeamStageConfig,
    apply_red_team_stage,
)
from synthorg.engine.completion_oracle.evaluator import BuildTestOracle
from synthorg.engine.completion_oracle.protocol import CompletionOracleGate
from synthorg.engine.review.models import PipelineResult, ReviewVerdict
from synthorg.engine.review_gate_inputs import DeliverableReviewInputBuilder
from synthorg.observability import get_logger
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_REVIEW_COMPLETED,
    APPROVAL_GATE_REVIEW_REWORK,
)
from synthorg.observability.events.review_pipeline import (
    APPROVAL_GATE_PIPELINE_ALL_SKIPPED,
)
from synthorg.observability.events.vision_verify import (
    VISION_GATE_SKIPPED,
    VISION_REWORK_ROUTED,
)
from synthorg.persistence.code_execution_protocol import CodeExecutionRecordRepository
from synthorg.security.redteam.protocol import RedTeamGate
from synthorg.security.visionverify.models import VisionReviewInput
from synthorg.security.visionverify.protocol import VisionVerifierGate

logger = get_logger(__name__)


async def run_completion_gates(  # noqa: PLR0913 -- gate chain inputs, all required
    *,
    build_test_gate: BuildTestOracle | None = None,
    code_execution_records: CodeExecutionRecordRepository | None = None,
    completion_oracle_gate: CompletionOracleGate | None = None,
    completion_oracle_shadow_mode: bool = False,
    completion_oracle_min_stakes: Stakes = Stakes.LOW,
    red_team_gate: RedTeamGate | None,
    vision_gate: VisionVerifierGate | None,
    deliverable_input_builder: DeliverableReviewInputBuilder | None,
    on_missing_deliverable: Literal["block", "skip"],
    task: Task,
    target: TaskStatus,
    transition_reason: str,
    event: str,
    approved: bool,
    vision_input: VisionReviewInput | None,
    red_team_min_stakes: Stakes,
) -> GateOutcome:
    """Run the completion-oracle gates before a COMPLETED transition.

    The chain, in order (cheapest and most objective first): the
    execution-grounded build/test gate, the agent-session peer-review gate,
    then the adversarial red-team gate and the vision gate. The build/test
    and peer-review gates are the completion oracle: "done" means the code
    builds and tests pass AND an independent reviewer approved. The build/test
    gate fails CLOSED (a failing or unverified code task blocks); the
    peer-review gate never silently passes (a REJECT or ESCALATE reworks the
    task). The red-team / vision gates keep their existing fail-OPEN posture.

    When the incoming verdict is already a rejection, returns immediately.
    Each deliverable-consuming gate is stakes-gated by its own threshold; the
    deliverable input is built once and shared. A BLOCK / REJECT / failing
    verdict from any gate reroutes the task to IN_PROGRESS rework.

    Returns:
        The (possibly rerouted) ``(target, reason, event, approved)`` tuple.
    """
    outcome = GateOutcome(
        target=target,
        transition_reason=transition_reason,
        event=event,
        approved=approved,
    )
    if not outcome.approved:
        return outcome

    if build_test_gate is not None:
        outcome = await apply_build_test_gate(
            gate=build_test_gate,
            records=code_execution_records,
            task=task,
            outcome=outcome,
        )
        if not outcome.approved:
            return outcome

    # Local because ``output_style`` reaches back into the engine; the stage
    # itself documents why the deliverable is resolved once and shared.
    from synthorg.engine.output_style import (  # noqa: PLC0415
        output_policy_active as _output_policy_active,
    )

    red_team = RedTeamStageConfig(
        gate=red_team_gate,
        input_builder_wired=deliverable_input_builder is not None,
        on_missing_deliverable=on_missing_deliverable,
        min_stakes=red_team_min_stakes,
    )
    outcome, deliverable_input = await apply_oracle_review_stage(
        oracle=OracleStageConfig(
            gate=completion_oracle_gate,
            shadow_mode=completion_oracle_shadow_mode,
            min_stakes=completion_oracle_min_stakes,
            records=code_execution_records,
        ),
        deliverable_input_builder=deliverable_input_builder,
        red_team_active=red_team.armed_for(task),
        output_policy_active=_output_policy_active(),
        task=task,
        outcome=outcome,
    )
    if not outcome.approved:
        # Returned whole, so the oracle's own blocked reason survives: an
        # unstaffed park and a human escalation are answered differently.
        return outcome
    return await _apply_post_review_stages(
        red_team=red_team,
        vision_gate=vision_gate,
        task=task,
        outcome=outcome,
        deliverable_input=deliverable_input,
        vision_input=vision_input,
    )


async def _apply_post_review_stages(
    *,
    red_team: RedTeamStageConfig,
    vision_gate: VisionVerifierGate | None,
    task: Task,
    outcome: GateOutcome,
    deliverable_input: RedTeamReviewInput | None,
    vision_input: VisionReviewInput | None,
) -> GateOutcome:
    """Run the stages that follow peer review, on the shared deliverable.

    Two deciding gates, in order: the adversarial red-team gate, then the
    vision gate. Every outcome is returned WHOLE rather than rebuilt from four
    fields, so whatever blocked reason the last gate to speak set survives: an
    unstaffed adversary parks for staffing, which is a different answer from
    rework.

    The output-style backstop runs alongside them and decides nothing: it is
    called for its observation and returns no outcome, because style is
    enforced in-session at the tool that wrote the file and a task whose
    substance passed review must not be failed over punctuation.

    Args:
        red_team: How the adversarial stage is wired.
        vision_gate: The vision verifier, when one is wired.
        task: The task being judged.
        outcome: The outcome peer review left.
        deliverable_input: The shared deliverable, when one was built.
        vision_input: The vision gate's own input, when it has one.

    Returns:
        The (possibly rerouted) outcome.
    """
    if not outcome.approved:
        return outcome
    observe_output_policy(deliverable=deliverable_input, task=task)
    outcome = await apply_red_team_stage(
        config=red_team,
        task=task,
        outcome=outcome,
        deliverable_input=deliverable_input,
    )
    if not outcome.approved:
        return outcome
    return await apply_vision_gate(
        gate=vision_gate,
        task_id=str(task.id),
        target=outcome.target,
        transition_reason=outcome.transition_reason,
        event=outcome.event,
        approved=outcome.approved,
        vision_input=vision_input,
    )


async def apply_vision_gate(
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
        return GateOutcome(target, transition_reason, event, approved)
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
        return GateOutcome(target, transition_reason, event, approved)

    from synthorg.security.visionverify.models import VisionVerdict  # noqa: PLC0415

    result = await gate.evaluate(vision_input)
    if result.verdict is not VisionVerdict.BLOCK:
        return GateOutcome(target, transition_reason, event, approved)
    logger.warning(
        VISION_REWORK_ROUTED,
        task_id=task_id,
        execution_id=vision_input.execution_id,
        findings=len(result.report.findings),
        verdict=result.verdict.value,
    )
    rework_reason = f"Vision review blocked completion: {result.report.summary}"
    return GateOutcome(
        target=TaskStatus.IN_PROGRESS,
        transition_reason=rework_reason,
        event=APPROVAL_GATE_REVIEW_REWORK,
        approved=False,
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
        return GateOutcome(
            target=TaskStatus.IN_PROGRESS,
            transition_reason=f"Pipeline rejected review by {decided_by}: {detail}",
            event=APPROVAL_GATE_REVIEW_REWORK,
            approved=False,
        )
    if result.final_verdict is ReviewVerdict.SKIP:
        logger.warning(
            APPROVAL_GATE_PIPELINE_ALL_SKIPPED,
            task_id=result.task_id,
            decided_by=decided_by,
        )
        stages = ", ".join(stage.stage_name for stage in result.stage_results)
        reason = f"Pipeline all-skipped ({stages or 'no stages'})"
        return GateOutcome(
            target=TaskStatus.COMPLETED,
            transition_reason=reason,
            event=APPROVAL_GATE_REVIEW_COMPLETED,
            approved=True,
        )
    stages = ", ".join(stage.stage_name for stage in result.stage_results)
    reason = (
        f"Pipeline passed ({stages})"
        if stages
        else "Pipeline passed (no stages configured)"
    )
    return GateOutcome(
        target=TaskStatus.COMPLETED,
        transition_reason=reason,
        event=APPROVAL_GATE_REVIEW_COMPLETED,
        approved=True,
    )
