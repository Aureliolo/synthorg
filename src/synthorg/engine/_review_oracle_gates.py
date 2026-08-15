# module-kind: code
"""Completion-oracle gate application: the build/test and peer-review gates.

Extracted from ``_review_completion_gates`` so the chain orchestrator stays
under its module-size budget. Holds the two oracle gates the completion chain
runs first (build/test, then peer review) plus the shared ``GateOutcome``
transition tuple and the deliverable-input adapter. Each gate returns the
(possibly rerouted) transition tuple ``(target, reason, event, approved)``.
"""

from typing import TYPE_CHECKING, NamedTuple

from synthorg.core.task import Task
from synthorg.core.task_enums import (
    BlockedReason,
    TaskStatus,
)
from synthorg.engine.completion_oracle.evaluator import BuildTestOracle
from synthorg.engine.completion_oracle.protocol import CompletionOracleGate
from synthorg.engine.completion_oracle.review_input import CompletionOracleReviewInput
from synthorg.engine.completion_oracle.review_models import (
    CompletionOracleGateResult,
    CompletionOracleVerdict,
)
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


class GateOutcome(NamedTuple):
    """What a gate decided: where the task goes, why, and under what reason.

    ``blocked_reason`` travels with the outcome rather than being stamped by
    the transition writer, because only the gate knows WHY it parked a task:
    an escalation waits on a human, an unstaffed role waits on staffing, and
    a rule written for one must not silently apply to the other. It is only
    read when ``target`` is BLOCKED.

    Attributes:
        target: The status the task transitions to.
        transition_reason: Recorded against the transition.
        event: Observability event name for the transition.
        approved: Whether the completion still stands.
        blocked_reason: Why the task is parked, when it is.
    """

    target: TaskStatus
    transition_reason: str
    event: str
    approved: bool
    blocked_reason: BlockedReason = BlockedReason.ORACLE_ESCALATED


def apply_output_policy_gate(
    *,
    deliverable: RedTeamReviewInput | None,
    task: Task,
    target: TaskStatus,
    transition_reason: str,
    event: str,
    approved: bool,
) -> GateOutcome:
    """Enforce the output-style policy on a completing deliverable (backstop).

    A deterministic, LLM-free defence-in-depth check on the deliverable prose
    that runs before the adversarial red-team / vision gates. It complements the
    per-tool interceptors: even a deliverable that reached completion by a path
    that skipped a guarded tool cannot ship with a hard-rule violation. A
    blocking verdict reroutes the task to IN_PROGRESS rework; a shadow or exempt
    finding never blocks. When the policy is unwired / disabled, or no
    deliverable was built, it passes through. Deferred import keeps the
    output-style leaf out of this module's cold-import set.

    Returns:
        The (possibly rerouted) ``(target, reason, event, approved)`` tuple.
    """
    if not approved or deliverable is None:
        return GateOutcome(target, transition_reason, event, approved)

    from synthorg.engine.output_style import (  # noqa: PLC0415
        OutputChannel,
        OutputContext,
        evaluate_output_policy,
    )

    ctx = OutputContext(
        channel=OutputChannel.DELIVERABLE,
        task_type=task.type.value,
        project_id=task.project,
    )
    # The agent's own prose, not the composed deliverable: the composed body
    # carries the produced source files, and a hard rule matching a character
    # inside one of them is not something the agent can rewrite.
    verdict = evaluate_output_policy(deliverable.agent_summary, ctx)
    if verdict is None:
        return GateOutcome(target, transition_reason, event, approved)
    # This backstop returns a transition, not content, so it cannot persist an
    # AUTO_REWRITE fix. A verdict that blocks, or that would rewrite the stored
    # deliverable, routes to rework so the agent regenerates compliant output
    # rather than shipping the original violating text.
    needs_rework = verdict.blocked or (
        verdict.rewritten_text is not None
        and verdict.rewritten_text != deliverable.agent_summary
    )
    if not needs_rework:
        return GateOutcome(target, transition_reason, event, approved)
    reason = verdict.summary or (
        "Output-style policy requires a compliant rewrite of the deliverable"
    )
    return GateOutcome(
        target=TaskStatus.IN_PROGRESS,
        transition_reason=f"Output-style policy blocked completion: {reason}",
        event=APPROVAL_GATE_REVIEW_REWORK,
        approved=False,
    )


def to_oracle_input(
    deliverable: RedTeamReviewInput | None,
    task: Task,
) -> CompletionOracleReviewInput | None:
    """Adapt a built deliverable review input for the peer-review gate.

    The deliverable builder is shared with the red-team gate; the executor's
    id (``assigned_agent_id`` on the red-team input) becomes the peer-review
    gate's ``executor_agent_id`` so it can enforce reviewer distinctness.

    The reviewed task's stakes and complexity travel with it, because they
    decide which role holder is capable enough to judge this work.

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
        stakes=task.stakes,
        estimated_complexity=task.estimated_complexity,
    )


async def apply_build_test_gate(
    *,
    gate: BuildTestOracle | None,
    records: CodeExecutionRecordRepository | None,
    task: Task,
    outcome: GateOutcome,
) -> GateOutcome:
    """Invoke the build/test oracle; block a failing / unverified code task.

    Fails CLOSED: a REQUIRED code task whose tests failed or never ran
    reroutes to IN_PROGRESS rework. A non-code task, a passing code task, or
    an unwired record store leaves the target unchanged (see
    :meth:`BuildTestOracle.evaluate`).

    Args:
        gate: The build/test oracle, when one is wired.
        records: The execution-record store the oracle reads.
        task: The task being judged.
        outcome: The incoming outcome, preserved when nothing blocks.

    Returns:
        The (possibly rerouted) ``(target, reason, event, approved)``.
    """
    if gate is None:
        return outcome
    evaluation = await gate.evaluate(task, records=records)
    if not evaluation.blocks_completion:
        return outcome
    logger.warning(
        BUILD_TEST_GATE_BLOCKED,
        task_id=str(task.id),
        verdict=evaluation.verdict.value,
        reason=evaluation.reason,
    )
    return GateOutcome(
        target=TaskStatus.IN_PROGRESS,
        transition_reason=f"Build/test oracle blocked completion: {evaluation.reason}",
        event=APPROVAL_GATE_REVIEW_REWORK,
        approved=False,
    )


async def apply_completion_oracle_gate(
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
        return GateOutcome(target, transition_reason, event, approved)
    result = await gate.evaluate(review_input)
    if shadow_mode:
        logger.info(
            COMPLETION_ORACLE_SHADOW_OBSERVED,
            task_id=task_id,
            execution_id=review_input.execution_id,
            verdict=result.verdict.value,
        )
        return GateOutcome(target, transition_reason, event, approved)
    if result.verdict in (
        CompletionOracleVerdict.APPROVE,
        CompletionOracleVerdict.APPROVE_WITH_NOTES,
    ):
        return GateOutcome(target, transition_reason, event, approved)
    return _route_non_approving_verdict(
        result, task_id=task_id, execution_id=review_input.execution_id
    )


def _route_non_approving_verdict(
    result: CompletionOracleGateResult,
    *,
    task_id: str,
    execution_id: str,
) -> GateOutcome:
    """Turn a REJECT or ESCALATE verdict into where the task goes.

    The split is the whole point of the gate: a REJECT names something the
    agent can fix, so it reroutes to rework; an ESCALATE names something it
    cannot, so it parks for a human. The two escalations park the same way
    but are answered by different people, so the reason travels with the
    outcome: a human decides an ordinary escalation, while an unstaffed role
    is answered by staffing it and MUST be re-judged afterwards.

    Args:
        result: The gate's non-approving result.
        task_id: The reviewed task, for the log.
        execution_id: The reviewed execution, for the log.

    Returns:
        The rerouted outcome.
    """
    if result.verdict is CompletionOracleVerdict.ESCALATE:
        blocked_reason = (
            BlockedReason.REVIEWER_UNSTAFFED
            if result.reviewer_unstaffed
            else BlockedReason.ORACLE_ESCALATED
        )
        logger.warning(
            COMPLETION_ORACLE_ESCALATION_ROUTED,
            task_id=task_id,
            execution_id=execution_id,
            verdict=result.verdict.value,
            findings=len(result.report.findings),
            blocked_reason=blocked_reason.value,
        )
        return GateOutcome(
            target=TaskStatus.BLOCKED,
            transition_reason=(
                f"Completion review escalated to a human decision: "
                f"{result.report.summary}"
            ),
            event=COMPLETION_ORACLE_ESCALATION_ROUTED,
            approved=False,
            blocked_reason=blocked_reason,
        )
    logger.warning(
        COMPLETION_ORACLE_REWORK_ROUTED,
        task_id=task_id,
        execution_id=execution_id,
        verdict=result.verdict.value,
        findings=len(result.report.findings),
    )
    return GateOutcome(
        target=TaskStatus.IN_PROGRESS,
        transition_reason=(
            f"Completion review ({result.verdict.value}): {result.report.summary}"
        ),
        event=APPROVAL_GATE_REVIEW_REWORK,
        approved=False,
    )
