# module-kind: code
"""Completion-oracle gate application: the build/test and peer-review gates.

Extracted from ``_review_completion_gates`` so the chain orchestrator stays
under its module-size budget. Holds the two oracle gates the completion chain
runs first (build/test, then peer review) plus the shared ``GateOutcome``
transition tuple and the deliverable-input adapter. Each gate returns the
(possibly rerouted) transition tuple ``(target, reason, event, approved)``.
"""

from typing import TYPE_CHECKING

from synthorg.core.task import Task
from synthorg.core.task_enums import Stakes, TaskStatus, compare_stakes
from synthorg.engine.completion_oracle.evaluator import BuildTestOracle
from synthorg.engine.completion_oracle.protocol import CompletionOracleGate
from synthorg.engine.completion_oracle.review_input import CompletionOracleReviewInput
from synthorg.engine.completion_oracle.review_models import CompletionOracleVerdict
from synthorg.engine.review_gate_inputs import DeliverableReviewInputBuilder
from synthorg.observability import get_logger
from synthorg.observability.events.approval_gate import APPROVAL_GATE_REVIEW_REWORK
from synthorg.observability.events.completion_oracle import (
    BUILD_TEST_GATE_BLOCKED,
    COMPLETION_ORACLE_ESCALATION_ROUTED,
    COMPLETION_ORACLE_GATE_SKIPPED,
    COMPLETION_ORACLE_REWORK_ROUTED,
    COMPLETION_ORACLE_SHADOW_OBSERVED,
)
from synthorg.persistence.code_execution_protocol import CodeExecutionRecordRepository

if TYPE_CHECKING:
    from synthorg.core.redteam_review_input import RedTeamReviewInput

logger = get_logger(__name__)

#: Transition tuple a gate returns: (target, reason, event, approved).
GateOutcome = tuple[TaskStatus, str, str, bool]


def to_oracle_input(
    deliverable: RedTeamReviewInput | None,
) -> CompletionOracleReviewInput | None:
    """Adapt a built deliverable review input for the peer-review gate.

    The deliverable builder is shared with the red-team gate; the executor's
    id (``assigned_agent_id`` on the red-team input) becomes the peer-review
    gate's ``executor_agent_id`` so it can enforce reviewer distinctness.

    Returns:
        The peer-review input, or ``None`` when no deliverable was built.
    """
    if deliverable is None:
        return None
    return CompletionOracleReviewInput(
        task_id=deliverable.task_id,
        execution_id=deliverable.execution_id,
        deliverable_content=deliverable.deliverable_content,
        acceptance_criteria=deliverable.acceptance_criteria,
        executor_agent_id=deliverable.assigned_agent_id,
        project_id=deliverable.project_id,
    )


async def apply_build_test_gate(  # noqa: PLR0913 -- gate inputs, all required
    *,
    gate: BuildTestOracle | None,
    records: CodeExecutionRecordRepository | None,
    task: Task,
    target: TaskStatus,
    transition_reason: str,
    event: str,
    approved: bool,
) -> GateOutcome:
    """Invoke the build/test oracle; block a failing / unverified code task.

    Fails CLOSED: a REQUIRED code task whose tests failed or never ran
    reroutes to IN_PROGRESS rework. A non-code task, a passing code task, or
    an unwired record store leaves the target unchanged (see
    :meth:`BuildTestOracle.evaluate`).

    Returns:
        The (possibly rerouted) ``(target, reason, event, approved)``.
    """
    if gate is None:
        return target, transition_reason, event, approved
    evaluation = await gate.evaluate(task, records=records)
    if not evaluation.blocks_completion:
        return target, transition_reason, event, approved
    logger.warning(
        BUILD_TEST_GATE_BLOCKED,
        task_id=str(task.id),
        verdict=evaluation.verdict.value,
        reason=evaluation.reason,
    )
    return (
        TaskStatus.IN_PROGRESS,
        f"Build/test oracle blocked completion: {evaluation.reason}",
        APPROVAL_GATE_REVIEW_REWORK,
        False,
    )


async def apply_completion_oracle_gate(  # noqa: PLR0913 -- gate inputs, all required
    *,
    gate: CompletionOracleGate | None,
    review_input: CompletionOracleReviewInput | None,
    shadow_mode: bool,
    task_id: str,
    target: TaskStatus,
    transition_reason: str,
    event: str,
    approved: bool,
) -> GateOutcome:
    """Invoke the peer-review gate; rework on REJECT, park on ESCALATE.

    Never silently passes. The two non-approving verdicts route to distinct
    outcomes: a REJECT (criteria unmet / tests fail / stub) is agent-actionable,
    so it reroutes to IN_PROGRESS rework; an ESCALATE (no confident verdict, no
    distinct reviewer resolvable, or a reviewer fault) is *not* something the
    agent can fix by reworking, so it parks the task at BLOCKED for a human
    decision. APPROVE / APPROVE_WITH_NOTES leaves the target unchanged. In
    shadow mode the verdict is computed and surfaced but never enforced.

    Returns:
        The (possibly rerouted) ``(target, reason, event, approved)``.
    """
    if gate is None or review_input is None:
        return target, transition_reason, event, approved
    result = await gate.evaluate(review_input)
    if shadow_mode:
        logger.info(
            COMPLETION_ORACLE_SHADOW_OBSERVED,
            task_id=task_id,
            execution_id=review_input.execution_id,
            verdict=result.verdict.value,
        )
        return target, transition_reason, event, approved
    if result.verdict in (
        CompletionOracleVerdict.APPROVE,
        CompletionOracleVerdict.APPROVE_WITH_NOTES,
    ):
        return target, transition_reason, event, approved
    if result.verdict is CompletionOracleVerdict.ESCALATE:
        logger.warning(
            COMPLETION_ORACLE_ESCALATION_ROUTED,
            task_id=task_id,
            execution_id=review_input.execution_id,
            verdict=result.verdict.value,
            findings=len(result.report.findings),
        )
        return (
            TaskStatus.BLOCKED,
            f"Completion review escalated to a human decision: {result.report.summary}",
            COMPLETION_ORACLE_ESCALATION_ROUTED,
            False,
        )
    logger.warning(
        COMPLETION_ORACLE_REWORK_ROUTED,
        task_id=task_id,
        execution_id=review_input.execution_id,
        verdict=result.verdict.value,
        findings=len(result.report.findings),
    )
    return (
        TaskStatus.IN_PROGRESS,
        f"Completion review ({result.verdict.value}): {result.report.summary}",
        APPROVAL_GATE_REVIEW_REWORK,
        False,
    )


async def apply_oracle_review_stage(  # noqa: PLR0913 -- stage inputs, all required
    *,
    completion_oracle_gate: CompletionOracleGate | None,
    completion_oracle_shadow_mode: bool,
    completion_oracle_min_stakes: Stakes,
    deliverable_input_builder: DeliverableReviewInputBuilder | None,
    red_team_active: bool,
    task: Task,
    outcome: GateOutcome,
) -> tuple[GateOutcome, RedTeamReviewInput | None]:
    """Run the peer-review gate and hand back the shared deliverable input.

    Resolves the reviewable deliverable ONCE (shared with the downstream
    red-team gate, so a completion where both gates are active pays a single
    retrieval) whenever the oracle is active at this task's stakes or the
    red-team gate will consume it. An ENFORCED (non-shadow) oracle fails CLOSED
    whenever no deliverable is retrievable -- whether the builder returned
    ``None`` or none is wired -- because the peer-review gate would otherwise
    receive a ``None`` input and silently preserve approval, letting the task
    reach COMPLETED without the independent review the oracle promises. Shadow
    mode only observes, so it never blocks. Then applies the stakes-gated
    peer-review gate.

    Returns:
        The (possibly rerouted) ``(target, reason, event, approved)`` tuple and
        the built deliverable input (``None`` when neither gate needed it), so
        the caller's red-team gate can reuse it without a second retrieval.
    """
    target, transition_reason, event, approved = outcome
    oracle_active = (
        completion_oracle_gate is not None
        and compare_stakes(task.stakes, completion_oracle_min_stakes) >= 0
    )
    deliverable_input = (
        await deliverable_input_builder.build(task)
        if deliverable_input_builder is not None and (oracle_active or red_team_active)
        else None
    )
    if (
        oracle_active
        and deliverable_input is None
        and not completion_oracle_shadow_mode
    ):
        # Fail CLOSED on enforcement mode, not builder presence: an enforced
        # oracle that cannot obtain a reviewable deliverable -- whether the
        # builder returned None OR none is wired at all -- must not let the task
        # reach COMPLETED unreviewed. Shadow mode only observes, so it never
        # blocks and preserves the incoming outcome (handled below).
        logger.warning(
            COMPLETION_ORACLE_GATE_SKIPPED,
            task_id=str(task.id),
            reason="no_deliverable_block",
            note=(
                "Completion oracle is active but no reviewable deliverable was "
                "retrievable; blocking completion (fail-closed)."
            ),
        )
        return (
            (
                TaskStatus.IN_PROGRESS,
                "Completion review could not retrieve a deliverable to inspect.",
                APPROVAL_GATE_REVIEW_REWORK,
                False,
            ),
            deliverable_input,
        )
    if completion_oracle_gate is None:
        return (target, transition_reason, event, approved), deliverable_input
    if not oracle_active:
        logger.info(
            COMPLETION_ORACLE_GATE_SKIPPED,
            task_id=str(task.id),
            reason="below_stakes_threshold",
            stakes=task.stakes.value,
            min_stakes=completion_oracle_min_stakes.value,
        )
        return (target, transition_reason, event, approved), deliverable_input
    outcome = await apply_completion_oracle_gate(
        gate=completion_oracle_gate,
        review_input=to_oracle_input(deliverable_input),
        shadow_mode=completion_oracle_shadow_mode,
        task_id=str(task.id),
        target=target,
        transition_reason=transition_reason,
        event=event,
        approved=approved,
    )
    return outcome, deliverable_input
