# module-kind: code
"""The adversarial stage of the completion-gate chain.

A sibling of ``_review_completion_gates`` rather than part of it: the
red-team stage is the one gate in the chain that can park a task instead of
reworking it, and its three "did not run" branches each name a different
operator condition. Keeping it here lets the chain module stay a chain.

Each function returns the (possibly rerouted) transition tuple
``(target, reason, event, approved)``.
"""

from typing import TYPE_CHECKING, Literal, NamedTuple

from synthorg.core.task import Task
from synthorg.core.task_enums import (
    BlockedReason,
    Stakes,
    TaskStatus,
    compare_stakes,
)
from synthorg.engine._review_oracle_gates import GateOutcome
from synthorg.observability import get_logger
from synthorg.observability.events.approval_gate import APPROVAL_GATE_REVIEW_REWORK
from synthorg.observability.events.red_team import (
    RED_TEAM_GATE_SKIPPED,
    RED_TEAM_NO_DELIVERABLE,
    RED_TEAM_REWORK_ROUTED,
    RED_TEAM_UNSTAFFED,
)
from synthorg.security.redteam.protocol import RedTeamGate

if TYPE_CHECKING:
    from synthorg.core.redteam_review_input import RedTeamReviewInput

logger = get_logger(__name__)


class RedTeamStageConfig(NamedTuple):
    """How the adversarial stage is wired, as one value.

    The four travel together on every call, and the stage is meaningless
    without all of them: a gate with no builder is inert, and a posture or a
    stakes floor without a gate decides nothing.

    Attributes:
        gate: The red-team gate, when one is wired.
        input_builder_wired: Whether a deliverable builder exists at all.
        on_missing_deliverable: Posture when no deliverable was retrievable.
        min_stakes: The stakes floor the gate is armed at.
    """

    gate: RedTeamGate | None
    input_builder_wired: bool
    on_missing_deliverable: Literal["block", "skip"]
    min_stakes: Stakes

    def armed_for(self, task: Task) -> bool:
        """Whether the stage will consume a deliverable for this task.

        The chain builds the deliverable up front to share it, so it has to
        answer this question before the stage runs, and the stage answers it
        again to decide whether to run. Two copies that drifted would arm the
        stage on a deliverable nobody built, and the gate fails CLOSED on a
        missing one, so the drift would read as a security finding.

        Args:
            task: The task being judged.

        Returns:
            ``True`` when a gate and a builder are both wired and the task's
            stakes reach the configured floor.
        """
        return (
            self.gate is not None
            and self.input_builder_wired
            and compare_stakes(task.stakes, self.min_stakes) >= 0
        )


async def apply_red_team_stage(
    *,
    config: RedTeamStageConfig,
    task: Task,
    outcome: GateOutcome,
    deliverable_input: RedTeamReviewInput | None,
) -> GateOutcome:
    """Run the adversarial gate when it is wired and armed at these stakes.

    Both ways of not running are logged, because each names a different
    operator condition: below-threshold stakes is the gate working as
    configured, while an attached gate with no input builder is inert
    wiring nobody asked for.

    Args:
        config: How the stage is wired.
        task: The task being judged.
        outcome: The incoming outcome, preserved when the gate does not run.
        deliverable_input: The shared deliverable, when one was built.

    Returns:
        The (possibly rerouted) outcome.
    """
    if config.armed_for(task):
        return await apply_red_team_gate(
            gate=config.gate,
            on_missing_deliverable=config.on_missing_deliverable,
            task_id=str(task.id),
            target=outcome.target,
            transition_reason=outcome.transition_reason,
            event=outcome.event,
            approved=outcome.approved,
            red_team_input=deliverable_input,
        )
    if config.gate is None:
        return outcome
    if not config.input_builder_wired:
        logger.warning(
            RED_TEAM_GATE_SKIPPED,
            task_id=str(task.id),
            reason="input_builder_not_wired",
            note=(
                "Red-team gate is attached but no input builder is wired "
                "(e.g. persistence absent); gate is inert this run."
            ),
        )
        return outcome
    logger.info(
        RED_TEAM_GATE_SKIPPED,
        task_id=str(task.id),
        reason="below_stakes_threshold",
        stakes=task.stakes.value,
        min_stakes=config.min_stakes.value,
        note=(
            "Red-team gate is wired but the task's stakes are below "
            "the configured red_team_min_stakes threshold; the "
            "adversarial review is reserved for higher-stakes work."
        ),
    )
    return outcome


def _missing_deliverable_outcome(
    on_missing_deliverable: Literal["block", "skip"],
    *,
    task_id: str,
    unchanged: GateOutcome,
) -> GateOutcome:
    """Decide what a configured red-team gate does with nothing to inspect.

    The posture is the operator's call, and both halves are loud: ``"block"``
    fails closed, because a configured security gate must not pass a
    deliverable it could not read, while ``"skip"`` leaves the incoming
    outcome alone.

    Args:
        on_missing_deliverable: The configured posture.
        task_id: The task being judged, for the log.
        unchanged: The outcome to preserve when skipping.

    Returns:
        Either *unchanged* or the fail-closed rework outcome.
    """
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
        return unchanged
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
    return GateOutcome(
        target=TaskStatus.IN_PROGRESS,
        transition_reason=(
            "Red-team review could not retrieve a deliverable to inspect."
        ),
        event=APPROVAL_GATE_REVIEW_REWORK,
        approved=False,
    )


async def apply_red_team_gate(
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
    unchanged = GateOutcome(
        target=target,
        transition_reason=transition_reason,
        event=event,
        approved=approved,
    )
    if gate is None:
        return unchanged
    if red_team_input is None:
        return _missing_deliverable_outcome(
            on_missing_deliverable,
            task_id=task_id,
            unchanged=unchanged,
        )

    from synthorg.security.redteam.models import RedTeamVerdict  # noqa: PLC0415

    result = await gate.evaluate(red_team_input)
    if result.verdict is not RedTeamVerdict.BLOCK:
        return unchanged
    if result.red_team_unstaffed:
        # Not rework: the agent cannot staff a role. Park it under its own
        # reason so the staffing sweep releases it once somebody holds the
        # role, rather than bouncing the deliverable back to its author with
        # a rework instruction nothing in the deliverable can satisfy.
        logger.warning(
            RED_TEAM_UNSTAFFED,
            task_id=task_id,
            execution_id=red_team_input.execution_id,
            blocked_reason=BlockedReason.RED_TEAM_UNSTAFFED.value,
        )
        return GateOutcome(
            target=TaskStatus.BLOCKED,
            transition_reason=(
                f"Adversarial review could not run: {result.report.summary}"
            ),
            event=RED_TEAM_UNSTAFFED,
            approved=False,
            blocked_reason=BlockedReason.RED_TEAM_UNSTAFFED,
        )
    logger.warning(
        RED_TEAM_REWORK_ROUTED,
        task_id=task_id,
        execution_id=red_team_input.execution_id,
        findings=len(result.report.findings),
        verdict=result.verdict.value,
    )
    rework_reason = f"Red-team review blocked completion: {result.report.summary}"
    return GateOutcome(
        target=TaskStatus.IN_PROGRESS,
        transition_reason=rework_reason,
        event=APPROVAL_GATE_REVIEW_REWORK,
        approved=False,
    )


__all__ = ["RedTeamStageConfig", "apply_red_team_gate", "apply_red_team_stage"]
