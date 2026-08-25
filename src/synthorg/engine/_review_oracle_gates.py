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
from synthorg.observability.events.output_style import (
    OUTPUT_STYLE_BACKSTOP_OBSERVED,
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


def observe_output_policy(
    *,
    deliverable: RedTeamReviewInput | None,
    task: Task,
) -> None:
    """Observe the output-style policy over a completing deliverable's files.

    A shadow backstop, deliberately: it reports and never decides. Enforcement
    belongs at the tool that writes the thing, where a refusal comes back as a
    tool result the agent fixes on its next turn. Here the session has already
    ended, so the only correction available is a whole re-dispatch, and a style
    nit would destroy work whose substance a peer reviewer already approved.
    It returns nothing and takes no transition, so that promise is structural
    rather than a docstring: there is no outcome for it to rewrite.

    It still exists because a whole class of writes produces no in-session
    signal at all. An agent given the shell tool writes files inside the
    sandbox and commits through it, so no boundary in this process sees the
    bytes; the post-session read over the produced files is the only
    observation available for anything that did not pass a file tool.

    It reads the produced files, one per declared path, never the agent's
    closing message. Narration and reasoning are working state, not output:
    a task once failed after three rework rounds over four em-dashes in a
    sentence nobody keeps. Per file, so an operator's PATH exemption applies
    and a finding names the file to fix. Deferred import keeps the
    output-style leaf out of this module's cold-import set.

    Args:
        deliverable: The built deliverable, or ``None`` when none was.
        task: The task whose completion is being judged.
    """
    if deliverable is None:
        return

    from synthorg.engine.output_style import (  # noqa: PLC0415
        OutputChannel,
        OutputContext,
        evaluate_output_policy,
    )

    rule_ids: set[str] = set()
    paths: list[str] = []
    for artifact in deliverable.produced_artifacts:
        ctx = OutputContext(
            channel=OutputChannel.CODE_FILE,
            file_path=artifact.path,
            task_type=task.type.value,
            project_id=task.project,
        )
        verdict = evaluate_output_policy(artifact.content, ctx)
        if verdict is None or not verdict.blocked:
            continue
        paths.append(artifact.path)
        rule_ids.update(f.rule_id for f in verdict.findings if f.blocks)
    if paths:
        # One line per deliverable rather than per finding: the interceptor's
        # own audit already emitted the per-finding events, and this says the
        # thing they cannot, that the violation survived to delivery.
        logger.warning(
            OUTPUT_STYLE_BACKSTOP_OBSERVED,
            task_id=str(task.id),
            rule_ids=sorted(rule_ids),
            paths=paths,
            file_count=len(paths),
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
