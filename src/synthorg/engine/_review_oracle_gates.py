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
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.completion_oracle.evaluator import BuildTestOracle
from synthorg.engine.completion_oracle.protocol import CompletionOracleGate
from synthorg.engine.completion_oracle.review_input import CompletionOracleReviewInput
from synthorg.engine.completion_oracle.review_models import CompletionOracleVerdict
from synthorg.observability import get_logger
from synthorg.observability.events.approval_gate import APPROVAL_GATE_REVIEW_REWORK
from synthorg.observability.events.completion_oracle import (
    BUILD_TEST_GATE_BLOCKED,
    COMPLETION_ORACLE_ESCALATION_ROUTED,
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
