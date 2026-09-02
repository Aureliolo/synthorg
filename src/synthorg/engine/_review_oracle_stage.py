# module-kind: code
"""The peer-review stage of the completion-gate chain.

A sibling of ``_review_oracle_gates`` rather than part of it: that module
holds the gates themselves, while this one owns the surrounding decision of
whether the judge runs at all and where the single shared deliverable comes
from. Splitting them keeps each within its module-size budget and puts the
"does this run" question next to the three separate reasons it can answer no.

Returns the (possibly rerouted) transition tuple
``(target, reason, event, approved)`` alongside the deliverable it built.
"""

import asyncio
from typing import Final, NamedTuple

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.redteam_review_input import RedTeamReviewInput
from synthorg.core.task import Task
from synthorg.core.task_enums import (
    BlockedReason,
    Stakes,
    TaskStatus,
    compare_stakes,
)
from synthorg.engine._review_oracle_gates import (
    GateOutcome,
    apply_completion_oracle_gate,
    to_oracle_input,
)
from synthorg.engine.completion_oracle.protocol import CompletionOracleGate
from synthorg.engine.review_gate_inputs import DeliverableReviewInputBuilder
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import APPROVAL_GATE_REVIEW_REWORK
from synthorg.observability.events.completion_oracle import (
    COMPLETION_ORACLE_EVIDENCE_UNREADABLE,
    COMPLETION_ORACLE_GATE_SKIPPED,
)
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionFilterSpec,
    CodeExecutionRecord,
    CodeExecutionRecordRepository,
)

logger = get_logger(__name__)

#: How many recorded runs a reviewer is shown. Newest first, so a project
#: that ran its suite many times still hands the reviewer the runs that
#: describe the tree as delivered rather than a history of attempts.
_VERIFICATION_RUN_LIMIT: Final[int] = 50


class OracleStageConfig(NamedTuple):
    """How the peer-review stage is wired, as one value.

    Attributes:
        gate: The peer-review gate, when one is wired.
        shadow_mode: Whether a verdict is observed but never enforced.
        min_stakes: The stakes floor the gate is armed at.
        records: The execution-record store the reviewer's evidence is read
            from, or ``None`` on a persistence-less boot. The reviewer holds
            no shell, so what it can cite about a build is exactly what this
            store recorded.
    """

    gate: CompletionOracleGate | None
    shadow_mode: bool
    min_stakes: Stakes
    records: CodeExecutionRecordRepository | None


async def _verification_runs(
    records: CodeExecutionRecordRepository | None,
    *,
    task: Task,
    execution_id: str,
) -> tuple[CodeExecutionRecord, ...]:
    """Read the runs the gates recorded for the reviewed execution.

    Args:
        records: The execution-record store, or ``None`` when none is wired.
        task: The task being judged.
        execution_id: The execution whose runs the reviewer may cite.

    Returns:
        The recorded runs, newest first, or none when nothing can be read.
        An unreadable store hands the reviewer no evidence, which the prompt
        tells it to read as unverified, so the failure is fail-closed at the
        verdict rather than masked.

    Raises:
        asyncio.CancelledError: Propagated when the query is cancelled.
    """
    if records is None:
        return ()
    spec = CodeExecutionFilterSpec(task_id=str(task.id), execution_id=execution_id)
    try:
        return await records.query(spec, limit=_VERIFICATION_RUN_LIMIT)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- an unreadable store hands the reviewer no
        # evidence, and the prompt makes no evidence a reject for code, so the
        # fault is fail-closed at the verdict rather than wedging completion
        # on a records-store blip.
        reraise_critical(exc)
        logger.warning(
            COMPLETION_ORACLE_EVIDENCE_UNREADABLE,
            task_id=str(task.id),
            execution_id=execution_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ()


def _resolve_oracle_activation(
    task: Task,
    *,
    gate: CompletionOracleGate | None,
    min_stakes: Stakes,
) -> tuple[bool, bool]:
    """Decide whether the judge runs on this task, and say why when it does not.

    Only THIS gate's own escalation is answered by the human's decision.
    Keyed on the recorded reason, never on the status: BLOCKED is reached
    from several directions (a coordination wave releasing a subtask is one,
    and an unstaffed reviewer role is another), and a status-only check
    silently exempted those from the verification this gate exists to impose.
    ``REVIEWER_UNSTAFFED`` deliberately does NOT qualify: nobody was ever
    asked, so there is no decision to preserve and the judge must run again.

    Args:
        task: The task whose completion is being judged.
        gate: The peer-review gate, when one is wired.
        min_stakes: The stakes floor the gate is armed at.

    Returns:
        ``(oracle_active, judge_already_ruled)``.
    """
    judge_already_ruled = (
        task.status is TaskStatus.BLOCKED
        and task.blocked_reason is BlockedReason.ORACLE_ESCALATED
    )
    if judge_already_ruled:
        # Re-running the judge on the human's answer re-escalates, which parks
        # the task again: the decision the escalation exists to obtain is
        # discarded by the rule that requested it. Whether a human is needed
        # and what the human decides are two separately owned questions; this
        # returns the second to its owner.
        #
        # Only the judge is skipped. The deliverable is still built by the
        # caller, because the red-team gate and the output-policy backstop are
        # different authorities that have not ruled on anything, and handing
        # them ``None`` reads as "retrieval failed": the red-team gate then
        # fails closed and reroutes the approval it was never asked about.
        logger.info(
            COMPLETION_ORACLE_GATE_SKIPPED,
            task_id=str(task.id),
            reason="human_decision_owns_an_escalated_task",
            note="task parked by an earlier escalation; the decision is the answer",
        )
    oracle_active = (
        gate is not None
        and not judge_already_ruled
        and compare_stakes(task.stakes, min_stakes) >= 0
    )
    return oracle_active, judge_already_ruled


def _no_deliverable_outcome(task: Task) -> GateOutcome:
    """Block completion when an enforced oracle has nothing to inspect.

    Fail CLOSED on enforcement mode, not builder presence: an enforced oracle
    that cannot obtain a reviewable deliverable, whether the builder returned
    ``None`` OR none is wired at all, must not let the task reach COMPLETED
    unreviewed. Shadow mode only observes, so it never reaches here and
    preserves the incoming outcome instead.

    Args:
        task: The task being judged, for the log.

    Returns:
        The rework outcome that keeps the task short of COMPLETED.
    """
    logger.warning(
        COMPLETION_ORACLE_GATE_SKIPPED,
        task_id=str(task.id),
        reason="no_deliverable_block",
        note=(
            "Completion oracle is active but no reviewable deliverable was "
            "retrievable; blocking completion (fail-closed)."
        ),
    )
    return GateOutcome(
        target=TaskStatus.IN_PROGRESS,
        transition_reason=(
            "Completion review could not retrieve a deliverable to inspect."
        ),
        event=APPROVAL_GATE_REVIEW_REWORK,
        approved=False,
    )


async def apply_oracle_review_stage(
    *,
    oracle: OracleStageConfig,
    deliverable_input_builder: DeliverableReviewInputBuilder | None,
    red_team_active: bool,
    output_policy_active: bool,
    task: Task,
    outcome: GateOutcome,
) -> tuple[GateOutcome, RedTeamReviewInput | None]:
    """Run the peer-review gate and hand back the shared deliverable input.

    Resolves the reviewable deliverable ONCE (shared with the downstream
    red-team gate and the output-policy backstop, so a completion where several
    consumers are active pays a single retrieval) whenever the oracle is active
    at this task's stakes, the red-team gate will consume it, or the
    output-policy backstop is enabled (the last is stakes-independent, so a
    low-stakes deliverable is still policy-checked). An ENFORCED (non-shadow)
    oracle fails CLOSED whenever no deliverable is retrievable -- whether the
    builder returned ``None`` or none is wired -- because the peer-review gate
    would otherwise receive a ``None`` input and silently preserve approval,
    letting the task reach COMPLETED without the independent review the oracle
    promises. Shadow mode only observes, so it never blocks. Then applies the
    stakes-gated peer-review gate.

    Returns:
        The (possibly rerouted) ``(target, reason, event, approved)`` tuple and
        the built deliverable input (``None`` when no consumer needed it), so
        the caller's red-team gate and output-policy backstop can reuse it
        without a second retrieval.
    """
    oracle_active, judge_already_ruled = _resolve_oracle_activation(
        task,
        gate=oracle.gate,
        min_stakes=oracle.min_stakes,
    )
    deliverable_input = (
        await deliverable_input_builder.build(task)
        if deliverable_input_builder is not None
        and (oracle_active or red_team_active or output_policy_active)
        else None
    )
    if oracle_active and deliverable_input is None and not oracle.shadow_mode:
        return _no_deliverable_outcome(task), deliverable_input
    if oracle.gate is None:
        return outcome, deliverable_input
    if not oracle_active:
        # The escalated case already logged its own reason above; saying
        # "below_stakes_threshold" here as well would name a cause that is
        # not the one that applied.
        if not judge_already_ruled:
            logger.info(
                COMPLETION_ORACLE_GATE_SKIPPED,
                task_id=str(task.id),
                reason="below_stakes_threshold",
                stakes=task.stakes.value,
                min_stakes=oracle.min_stakes.value,
            )
        return outcome, deliverable_input
    runs = (
        ()
        if deliverable_input is None
        else await _verification_runs(
            oracle.records,
            task=task,
            execution_id=deliverable_input.execution_id,
        )
    )
    return (
        await apply_completion_oracle_gate(
            gate=oracle.gate,
            review_input=to_oracle_input(
                deliverable_input, task, verification_runs=runs
            ),
            shadow_mode=oracle.shadow_mode,
            task_id=str(task.id),
            target=outcome.target,
            transition_reason=outcome.transition_reason,
            event=outcome.event,
            approved=outcome.approved,
        ),
        deliverable_input,
    )


__all__ = ["OracleStageConfig", "apply_oracle_review_stage"]
