"""Unit tests for the shared completion-gate chain.

Covers the chain-level control flow of ``run_completion_gates`` in
isolation from the full review-gate service: the already-rejected
short-circuit, and the "gate attached but input builder unwired" path
(for example a boot with no persistence) where the gate must stay inert
rather than fail-closed and block every completion.
"""

from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.redteam_review_input import RedTeamReviewInput
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import (
    BlockedReason,
    Priority,
    Stakes,
    TaskStatus,
    TaskType,
)
from synthorg.engine._review_completion_gates import run_completion_gates
from synthorg.engine._review_oracle_gates import apply_oracle_review_stage
from synthorg.engine.completion_oracle.build_test_models import (
    GroundingRequirement,
    OracleEvaluation,
    OracleVerdict,
)
from synthorg.engine.completion_oracle.evaluator import BuildTestOracle
from synthorg.engine.completion_oracle.protocol import CompletionOracleGate
from synthorg.engine.completion_oracle.review_models import (
    CompletionOracleGateResult,
    CompletionOracleReport,
    CompletionOracleVerdict,
)
from synthorg.engine.review_gate_inputs import DeliverableReviewInputBuilder
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_REVIEW_COMPLETED,
)
from synthorg.observability.events.completion_oracle import (
    COMPLETION_ORACLE_ESCALATION_ROUTED,
)
from synthorg.security.redteam.models import RedTeamVerdict
from synthorg.security.redteam.protocol import RedTeamGate
from tests._shared import as_uuid, mock_of

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_output_policy_ambient() -> Iterator[None]:
    """Reset the process-global output-policy service around every test.

    The completion backstop builds a deliverable whenever the policy is active,
    so a service another test (e.g. the session-scoped API app on the same
    xdist worker) left bound would otherwise make the stakes-gating assertions
    non-deterministic. Reset to unbound, restoring whatever was bound before.
    """
    from synthorg.engine.output_style import (
        current_output_policy_service,
        set_output_policy_service,
    )

    previous = current_output_policy_service()
    set_output_policy_service(None)
    try:
        yield
    finally:
        set_output_policy_service(previous)


def _task(
    *,
    stakes: Stakes = Stakes.NORMAL,
    status: TaskStatus = TaskStatus.IN_REVIEW,
    blocked_reason: BlockedReason | None = None,
) -> Task:
    return Task(
        id=as_uuid("task-1"),
        title="Service",
        description="A development task.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="alice",
        assigned_to="agent-backend",
        status=status,
        blocked_reason=blocked_reason,
        stakes=stakes,
        acceptance_criteria=(
            AcceptanceCriterion(description="Login endpoint exposed."),
        ),
    )


async def test_an_escalated_task_is_not_re_judged_by_the_gate_that_escalated() -> None:
    """The deadlock this closes.

    A judge that escalates parks the task at BLOCKED for a human. Re-running
    it on the human's answer re-escalates and parks it again, so the decision
    the escalation exists to obtain is discarded by the rule that asked for
    it. Whether a human is needed and what the human decides are two
    separately owned questions; this returns the second to its owner.
    """
    gate = mock_of[CompletionOracleGate](evaluate=AsyncMock())
    builder = mock_of[DeliverableReviewInputBuilder](
        build=AsyncMock(return_value=_deliverable()),
    )

    (target, _reason, _event, approved), _input = await apply_oracle_review_stage(
        completion_oracle_gate=gate,
        completion_oracle_shadow_mode=False,
        completion_oracle_min_stakes=Stakes.LOW,
        deliverable_input_builder=builder,
        red_team_active=False,
        output_policy_active=False,
        task=_task(
            status=TaskStatus.BLOCKED,
            blocked_reason=BlockedReason.ORACLE_ESCALATED,
        ),
        outcome=(
            TaskStatus.COMPLETED,
            "approved by the human the escalation asked for",
            APPROVAL_GATE_REVIEW_COMPLETED,
            True,
        ),
    )

    gate.evaluate.assert_not_awaited()
    assert target is TaskStatus.COMPLETED
    assert approved is True


@pytest.mark.parametrize(
    "blocked_reason",
    [BlockedReason.WAVE_RELEASED, None],
    ids=["released_by_a_wave", "reason_never_named"],
)
async def test_a_task_blocked_for_another_reason_is_still_judged(
    blocked_reason: BlockedReason | None,
) -> None:
    """The skip answers this gate's OWN escalation, not the status.

    BLOCKED is reached from several directions. A coordination wave releasing
    a subtask parks a task there having asked no human anything, and an older
    row may name no reason at all. Keyed on the status alone, the skip exempted
    both from the review it exists to impose, and the human-decision path could
    then carry them to COMPLETED with the judge never invoked.
    """
    gate = mock_of[CompletionOracleGate](evaluate=AsyncMock())
    builder = mock_of[DeliverableReviewInputBuilder](
        build=AsyncMock(return_value=_deliverable()),
    )

    await apply_oracle_review_stage(
        completion_oracle_gate=gate,
        completion_oracle_shadow_mode=False,
        completion_oracle_min_stakes=Stakes.LOW,
        deliverable_input_builder=builder,
        red_team_active=False,
        output_policy_active=False,
        task=_task(status=TaskStatus.BLOCKED, blocked_reason=blocked_reason),
        outcome=(
            TaskStatus.COMPLETED,
            "approved",
            APPROVAL_GATE_REVIEW_COMPLETED,
            True,
        ),
    )

    gate.evaluate.assert_awaited()


async def test_the_escalated_skip_still_builds_the_deliverable() -> None:
    """The judge is skipped; the other authorities are not.

    ``None`` reads downstream as "retrieval failed", not "nobody needed it":
    the output-policy backstop silently no-ops and the red-team gate, whose
    default posture is to block when it cannot inspect a deliverable, reroutes
    a human's approval to rework and blames a deliverable that exists.
    """
    gate = mock_of[CompletionOracleGate](evaluate=AsyncMock())
    builder = mock_of[DeliverableReviewInputBuilder](
        build=AsyncMock(return_value=_deliverable()),
    )

    _outcome, deliverable = await apply_oracle_review_stage(
        completion_oracle_gate=gate,
        completion_oracle_shadow_mode=False,
        completion_oracle_min_stakes=Stakes.LOW,
        deliverable_input_builder=builder,
        red_team_active=True,
        output_policy_active=False,
        task=_task(
            status=TaskStatus.BLOCKED,
            blocked_reason=BlockedReason.ORACLE_ESCALATED,
        ),
        outcome=(
            TaskStatus.COMPLETED,
            "approved by the human the escalation asked for",
            APPROVAL_GATE_REVIEW_COMPLETED,
            True,
        ),
    )

    gate.evaluate.assert_not_awaited()
    assert deliverable is not None


async def test_rejection_short_circuits_without_evaluating_gates() -> None:
    """An incoming rejection returns unchanged and never touches a gate."""
    gate = mock_of[RedTeamGate](evaluate=AsyncMock())

    target, reason, event, approved = await run_completion_gates(
        red_team_gate=gate,
        vision_gate=None,
        deliverable_input_builder=None,
        on_missing_deliverable="block",
        task=_task(),
        target=TaskStatus.IN_PROGRESS,
        transition_reason="rejected upstream",
        event="evt",
        approved=False,
        vision_input=None,
        red_team_min_stakes=Stakes.HIGH,
    )

    assert (target, reason, event, approved) == (
        TaskStatus.IN_PROGRESS,
        "rejected upstream",
        "evt",
        False,
    )
    gate.evaluate.assert_not_awaited()


async def test_gate_without_input_builder_is_inert() -> None:
    """A gate attached without an input builder passes the completion.

    This is the no-persistence boot: the gate is attached (for the
    receipt store) but the flight-recorder deliverable source is absent,
    so there is no builder. The completion must proceed rather than block
    on an un-inspectable deliverable the operator never configured.
    """
    gate = mock_of[RedTeamGate](evaluate=AsyncMock())

    target, _reason, _event, approved = await run_completion_gates(
        red_team_gate=gate,
        vision_gate=None,
        deliverable_input_builder=None,
        on_missing_deliverable="block",
        task=_task(),
        target=TaskStatus.COMPLETED,
        transition_reason="approved",
        event=APPROVAL_GATE_REVIEW_COMPLETED,
        approved=True,
        vision_input=None,
        red_team_min_stakes=Stakes.HIGH,
    )

    assert (target, approved) == (TaskStatus.COMPLETED, True)
    gate.evaluate.assert_not_awaited()


async def test_below_stakes_threshold_skips_red_team_gate() -> None:
    """A wired gate does not fire when the task is below the stakes floor.

    The adversarial review is reserved for work at or above
    ``red_team_min_stakes`` (default HIGH), matching the routing layer that
    only marks ``red_team_required`` at that threshold. A NORMAL-stakes
    approval completes without the gate (or its input builder) running.
    """
    gate = mock_of[RedTeamGate](evaluate=AsyncMock())
    builder = mock_of[DeliverableReviewInputBuilder](build=AsyncMock())

    target, _reason, _event, approved = await run_completion_gates(
        red_team_gate=gate,
        vision_gate=None,
        deliverable_input_builder=builder,
        on_missing_deliverable="block",
        task=_task(stakes=Stakes.NORMAL),
        target=TaskStatus.COMPLETED,
        transition_reason="approved",
        event=APPROVAL_GATE_REVIEW_COMPLETED,
        approved=True,
        vision_input=None,
        red_team_min_stakes=Stakes.HIGH,
    )

    assert (target, approved) == (TaskStatus.COMPLETED, True)
    gate.evaluate.assert_not_awaited()
    builder.build.assert_not_awaited()


@pytest.mark.parametrize("stakes", [Stakes.HIGH, Stakes.CRITICAL])
async def test_at_or_above_stakes_threshold_runs_red_team_gate(
    stakes: Stakes,
) -> None:
    """The gate fires for work at or above the configured stakes floor.

    A PASS verdict leaves the COMPLETED transition unchanged; the point of
    the test is that the gate (and its input builder) were actually invoked
    for HIGH and CRITICAL stakes.
    """
    gate = mock_of[RedTeamGate](
        evaluate=AsyncMock(return_value=SimpleNamespace(verdict=RedTeamVerdict.PASS)),
    )
    builder = mock_of[DeliverableReviewInputBuilder](
        build=AsyncMock(return_value=_deliverable()),
    )

    target, _reason, _event, approved = await run_completion_gates(
        red_team_gate=gate,
        vision_gate=None,
        deliverable_input_builder=builder,
        on_missing_deliverable="block",
        task=_task(stakes=stakes),
        target=TaskStatus.COMPLETED,
        transition_reason="approved",
        event=APPROVAL_GATE_REVIEW_COMPLETED,
        approved=True,
        vision_input=None,
        red_team_min_stakes=Stakes.HIGH,
    )

    assert (target, approved) == (TaskStatus.COMPLETED, True)
    gate.evaluate.assert_awaited_once()
    builder.build.assert_awaited_once()


async def test_output_policy_checks_low_stakes_deliverable() -> None:
    """A blocking output policy reworks a low-stakes completion.

    The output-style backstop is stakes-independent, so the deliverable is
    built and policy-checked even when neither the oracle nor the red-team gate
    fires for a below-threshold task; a hard-rule violation must still route the
    task back to rework rather than reaching COMPLETED unchecked.
    """
    from synthorg.engine.output_style import (
        OutputStyleConfig,
        OutputStylePolicyService,
        current_output_policy_service,
        set_output_policy_service,
    )

    em_dash = chr(0x2014)
    builder = mock_of[DeliverableReviewInputBuilder](
        build=AsyncMock(return_value=_deliverable(f"ship {em_dash} now")),
    )
    previous = current_output_policy_service()
    set_output_policy_service(OutputStylePolicyService.from_config(OutputStyleConfig()))
    try:
        target, _reason, _event, approved = await run_completion_gates(
            red_team_gate=None,
            vision_gate=None,
            deliverable_input_builder=builder,
            on_missing_deliverable="block",
            task=_task(stakes=Stakes.NORMAL),
            target=TaskStatus.COMPLETED,
            transition_reason="approved",
            event=APPROVAL_GATE_REVIEW_COMPLETED,
            approved=True,
            vision_input=None,
            red_team_min_stakes=Stakes.HIGH,
        )
    finally:
        set_output_policy_service(previous)

    assert (target, approved) == (TaskStatus.IN_PROGRESS, False)
    builder.build.assert_awaited_once()


def _deliverable(content: str = "the deliverable") -> RedTeamReviewInput:
    return RedTeamReviewInput(
        task_id="task-1",
        execution_id="exec-1",
        deliverable_content=content,
        agent_summary=content,
        acceptance_criteria=("Login endpoint exposed.",),
        assigned_agent_id="agent-backend",
        autonomy=AutonomyLevel.SUPERVISED,
    )


def _oracle_result(verdict: CompletionOracleVerdict) -> CompletionOracleGateResult:
    report = CompletionOracleReport(
        execution_id="exec-1",
        task_id="task-1",
        reviewer_agent_id="completion-reviewer",
        executor_agent_id="agent-backend",
        verdict=verdict,
        summary="review complete",
    )
    return CompletionOracleGateResult(
        verdict=verdict, report=report, elapsed_seconds=0.0
    )


async def test_build_test_gate_blocks_failing_code_task() -> None:
    """A failing/unverified build/test verdict reworks the task, first in chain."""
    build_test_gate = mock_of[BuildTestOracle](
        evaluate=AsyncMock(
            return_value=OracleEvaluation(
                verdict=OracleVerdict.BUILD_TEST_FAILED,
                requirement=GroundingRequirement.REQUIRED,
                reason="latest test run failed",
            )
        ),
    )
    red_team = mock_of[RedTeamGate](evaluate=AsyncMock())

    target, _reason, _event, approved = await run_completion_gates(
        build_test_gate=build_test_gate,
        red_team_gate=red_team,
        vision_gate=None,
        deliverable_input_builder=None,
        on_missing_deliverable="block",
        task=_task(),
        target=TaskStatus.COMPLETED,
        transition_reason="approved",
        event=APPROVAL_GATE_REVIEW_COMPLETED,
        approved=True,
        vision_input=None,
        red_team_min_stakes=Stakes.HIGH,
    )

    assert (target, approved) == (TaskStatus.IN_PROGRESS, False)
    # The build/test gate runs first; a block short-circuits the rest.
    red_team.evaluate.assert_not_awaited()


async def test_completion_oracle_reject_reworks_task() -> None:
    """A peer-review REJECT reroutes the task to IN_PROGRESS rework."""
    oracle_gate = mock_of[CompletionOracleGate](
        evaluate=AsyncMock(return_value=_oracle_result(CompletionOracleVerdict.REJECT)),
    )
    builder = mock_of[DeliverableReviewInputBuilder](
        build=AsyncMock(return_value=_deliverable()),
    )

    target, _reason, _event, approved = await run_completion_gates(
        completion_oracle_gate=oracle_gate,
        completion_oracle_min_stakes=Stakes.LOW,
        red_team_gate=None,
        vision_gate=None,
        deliverable_input_builder=builder,
        on_missing_deliverable="block",
        task=_task(),
        target=TaskStatus.COMPLETED,
        transition_reason="approved",
        event=APPROVAL_GATE_REVIEW_COMPLETED,
        approved=True,
        vision_input=None,
        red_team_min_stakes=Stakes.HIGH,
    )

    assert (target, approved) == (TaskStatus.IN_PROGRESS, False)
    oracle_gate.evaluate.assert_awaited_once()


async def test_completion_oracle_shadow_mode_does_not_enforce() -> None:
    """In shadow mode a REJECT is surfaced but never reroutes the task."""
    oracle_gate = mock_of[CompletionOracleGate](
        evaluate=AsyncMock(return_value=_oracle_result(CompletionOracleVerdict.REJECT)),
    )
    builder = mock_of[DeliverableReviewInputBuilder](
        build=AsyncMock(return_value=_deliverable()),
    )

    target, _reason, _event, approved = await run_completion_gates(
        completion_oracle_gate=oracle_gate,
        completion_oracle_shadow_mode=True,
        completion_oracle_min_stakes=Stakes.LOW,
        red_team_gate=None,
        vision_gate=None,
        deliverable_input_builder=builder,
        on_missing_deliverable="block",
        task=_task(),
        target=TaskStatus.COMPLETED,
        transition_reason="approved",
        event=APPROVAL_GATE_REVIEW_COMPLETED,
        approved=True,
        vision_input=None,
        red_team_min_stakes=Stakes.HIGH,
    )

    assert (target, approved) == (TaskStatus.COMPLETED, True)
    oracle_gate.evaluate.assert_awaited_once()


async def test_completion_oracle_escalate_parks_for_human() -> None:
    """A peer-review ESCALATE parks the task at BLOCKED for a human decision.

    Distinct from a REJECT's agent rework (IN_PROGRESS): an ESCALATE is not
    something the agent can fix by redoing the work, so it routes to the
    human-decision transition instead.
    """
    oracle_gate = mock_of[CompletionOracleGate](
        evaluate=AsyncMock(
            return_value=_oracle_result(CompletionOracleVerdict.ESCALATE)
        ),
    )
    builder = mock_of[DeliverableReviewInputBuilder](
        build=AsyncMock(return_value=_deliverable()),
    )

    target, _reason, event, approved = await run_completion_gates(
        completion_oracle_gate=oracle_gate,
        completion_oracle_min_stakes=Stakes.LOW,
        red_team_gate=None,
        vision_gate=None,
        deliverable_input_builder=builder,
        on_missing_deliverable="block",
        task=_task(),
        target=TaskStatus.COMPLETED,
        transition_reason="approved",
        event=APPROVAL_GATE_REVIEW_COMPLETED,
        approved=True,
        vision_input=None,
        red_team_min_stakes=Stakes.HIGH,
    )

    assert (target, approved) == (TaskStatus.BLOCKED, False)
    assert event == COMPLETION_ORACLE_ESCALATION_ROUTED
    oracle_gate.evaluate.assert_awaited_once()


async def test_completion_oracle_no_deliverable_fails_closed() -> None:
    """A wired builder yielding no deliverable blocks completion (fail-closed).

    The peer-review gate would otherwise receive a ``None`` input and silently
    preserve approval, so the task reroutes to IN_PROGRESS before the gate runs.
    """
    oracle_gate = mock_of[CompletionOracleGate](evaluate=AsyncMock())
    builder = mock_of[DeliverableReviewInputBuilder](
        build=AsyncMock(return_value=None),
    )

    target, _reason, _event, approved = await run_completion_gates(
        completion_oracle_gate=oracle_gate,
        completion_oracle_min_stakes=Stakes.LOW,
        red_team_gate=None,
        vision_gate=None,
        deliverable_input_builder=builder,
        on_missing_deliverable="block",
        task=_task(),
        target=TaskStatus.COMPLETED,
        transition_reason="approved",
        event=APPROVAL_GATE_REVIEW_COMPLETED,
        approved=True,
        vision_input=None,
        red_team_min_stakes=Stakes.HIGH,
    )

    assert (target, approved) == (TaskStatus.IN_PROGRESS, False)
    oracle_gate.evaluate.assert_not_awaited()


async def test_completion_oracle_no_builder_enforced_fails_closed() -> None:
    """An enforced oracle with no input builder wired blocks completion.

    Fail-closed keys on enforcement mode, not builder presence: when the oracle
    is active but no deliverable is retrievable (here because no builder is
    wired at all), the task must not reach COMPLETED unreviewed.
    """
    oracle_gate = mock_of[CompletionOracleGate](evaluate=AsyncMock())

    target, _reason, _event, approved = await run_completion_gates(
        completion_oracle_gate=oracle_gate,
        completion_oracle_min_stakes=Stakes.LOW,
        red_team_gate=None,
        vision_gate=None,
        deliverable_input_builder=None,
        on_missing_deliverable="block",
        task=_task(),
        target=TaskStatus.COMPLETED,
        transition_reason="approved",
        event=APPROVAL_GATE_REVIEW_COMPLETED,
        approved=True,
        vision_input=None,
        red_team_min_stakes=Stakes.HIGH,
    )

    assert (target, approved) == (TaskStatus.IN_PROGRESS, False)
    oracle_gate.evaluate.assert_not_awaited()


async def test_completion_oracle_shadow_mode_no_deliverable_does_not_block() -> None:
    """Shadow mode never blocks, even when no deliverable is retrievable.

    Fail-closed is reserved for enforced oracles; a shadow oracle only observes,
    so a missing deliverable preserves the incoming COMPLETED outcome rather
    than rerouting to rework.
    """
    oracle_gate = mock_of[CompletionOracleGate](evaluate=AsyncMock())
    builder = mock_of[DeliverableReviewInputBuilder](
        build=AsyncMock(return_value=None),
    )

    target, _reason, _event, approved = await run_completion_gates(
        completion_oracle_gate=oracle_gate,
        completion_oracle_shadow_mode=True,
        completion_oracle_min_stakes=Stakes.LOW,
        red_team_gate=None,
        vision_gate=None,
        deliverable_input_builder=builder,
        on_missing_deliverable="block",
        task=_task(),
        target=TaskStatus.COMPLETED,
        transition_reason="approved",
        event=APPROVAL_GATE_REVIEW_COMPLETED,
        approved=True,
        vision_input=None,
        red_team_min_stakes=Stakes.HIGH,
    )

    assert (target, approved) == (TaskStatus.COMPLETED, True)
    oracle_gate.evaluate.assert_not_awaited()


async def test_completion_oracle_below_min_stakes_skips() -> None:
    """A task below completion_oracle_min_stakes skips the peer review."""
    oracle_gate = mock_of[CompletionOracleGate](evaluate=AsyncMock())
    builder = mock_of[DeliverableReviewInputBuilder](
        build=AsyncMock(return_value=_deliverable()),
    )

    target, _reason, _event, approved = await run_completion_gates(
        completion_oracle_gate=oracle_gate,
        completion_oracle_min_stakes=Stakes.HIGH,
        red_team_gate=None,
        vision_gate=None,
        deliverable_input_builder=builder,
        on_missing_deliverable="block",
        task=_task(stakes=Stakes.NORMAL),
        target=TaskStatus.COMPLETED,
        transition_reason="approved",
        event=APPROVAL_GATE_REVIEW_COMPLETED,
        approved=True,
        vision_input=None,
        red_team_min_stakes=Stakes.HIGH,
    )

    assert (target, approved) == (TaskStatus.COMPLETED, True)
    oracle_gate.evaluate.assert_not_awaited()
