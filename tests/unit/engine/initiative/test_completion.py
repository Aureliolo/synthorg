"""Tests for the initiative completion rule (plan item done-ness)."""

import pytest

from synthorg.core.plan_enums import TAIL_STATUSES, PlanItemKind, PlanStatus
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.initiative.completion import (
    ItemProgress,
    StallReason,
    derive_plan_status,
    derive_project_status,
    item_is_done,
    stall_reason,
    summarise_progress,
)
from tests._shared import as_uuid

pytestmark = pytest.mark.unit


def _item(
    *,
    kind: PlanItemKind = PlanItemKind.WORK,
    task_status: TaskStatus | None = None,
    chosen_option_id: str | None = None,
) -> ItemProgress:
    return ItemProgress(
        item_id=as_uuid("item-1"),
        kind=kind,
        task_id=as_uuid("task-1") if kind is PlanItemKind.WORK else None,
        task_status=task_status,
        chosen_option_id=chosen_option_id,
    )


class TestItemDoneness:
    """A WORK item is done when its task passed; a DECISION when chosen."""

    def test_work_item_done_when_task_completed(self) -> None:
        assert item_is_done(_item(task_status=TaskStatus.COMPLETED)) is True

    @pytest.mark.parametrize(
        "status",
        [
            TaskStatus.CREATED,
            TaskStatus.ASSIGNED,
            TaskStatus.IN_PROGRESS,
            TaskStatus.BLOCKED,
            TaskStatus.FAILED,
            TaskStatus.AWAITING_INPUT,
            TaskStatus.AUTH_REQUIRED,
            TaskStatus.SUSPENDED,
            TaskStatus.INTERRUPTED,
            TaskStatus.CANCELLED,
            TaskStatus.REJECTED,
        ],
        ids=lambda s: s.value,
    )
    def test_work_item_not_done_for_any_non_completed_status(
        self, status: TaskStatus
    ) -> None:
        assert item_is_done(_item(task_status=status)) is False

    def test_work_item_in_review_is_not_done(self) -> None:
        """The verify gate composition, stated as an explicit invariant.

        A task sitting IN_REVIEW has executed but has not passed the
        completion oracle. Counting it as done is exactly the mistake the
        execution-derived coordination rollup makes; the initiative rollup
        must never make it, or a project could complete with unverified work.
        """
        assert item_is_done(_item(task_status=TaskStatus.IN_REVIEW)) is False

    def test_work_item_without_a_task_is_not_done(self) -> None:
        assert item_is_done(_item(task_status=None)) is False

    def test_decision_item_done_when_option_chosen(self) -> None:
        item = _item(kind=PlanItemKind.DECISION, chosen_option_id="opt-a")
        assert item_is_done(item) is True

    def test_decision_item_not_done_while_unresolved(self) -> None:
        item = _item(kind=PlanItemKind.DECISION, chosen_option_id=None)
        assert item_is_done(item) is False


class TestSummariseProgress:
    """Derived counts are the operator's attention signal."""

    def test_counts_done_failed_blocked_and_total(self) -> None:
        items = (
            _item(task_status=TaskStatus.COMPLETED),
            _item(task_status=TaskStatus.COMPLETED),
            _item(task_status=TaskStatus.FAILED),
            _item(task_status=TaskStatus.BLOCKED),
            _item(task_status=TaskStatus.IN_REVIEW),
            _item(kind=PlanItemKind.DECISION, chosen_option_id="opt-a"),
        )
        summary = summarise_progress(items)
        assert summary.total == 6
        assert summary.done == 3
        assert summary.failed == 1
        assert summary.blocked == 1

    def test_empty_plan_has_no_progress(self) -> None:
        summary = summarise_progress(())
        assert summary.total == 0
        assert summary.done == 0


class TestStallReason:
    """A stall is a shape (nothing can move), never a duration."""

    def test_all_outstanding_work_failed(self) -> None:
        items = (
            _item(task_status=TaskStatus.COMPLETED),
            _item(task_status=TaskStatus.FAILED),
            _item(task_status=TaskStatus.REJECTED),
        )
        assert stall_reason(items) is StallReason.ALL_FAILED

    def test_all_outstanding_work_blocked(self) -> None:
        items = (
            _item(task_status=TaskStatus.BLOCKED),
            _item(task_status=TaskStatus.SUSPENDED),
            _item(task_status=TaskStatus.INTERRUPTED),
        )
        assert stall_reason(items) is StallReason.BLOCKED

    def test_a_mix_of_dead_work_is_reported_as_mixed(self) -> None:
        items = (
            _item(task_status=TaskStatus.FAILED),
            _item(task_status=TaskStatus.BLOCKED),
        )
        assert stall_reason(items) is StallReason.MIXED_DEAD

    @pytest.mark.parametrize(
        "status",
        [
            TaskStatus.CREATED,
            TaskStatus.ASSIGNED,
            TaskStatus.IN_PROGRESS,
            TaskStatus.IN_REVIEW,
        ],
        ids=lambda s: s.value,
    )
    def test_one_item_still_moving_is_not_a_stall(self, status: TaskStatus) -> None:
        items = (_item(task_status=TaskStatus.FAILED), _item(task_status=status))
        assert stall_reason(items) is None

    @pytest.mark.parametrize(
        "status",
        [TaskStatus.AWAITING_INPUT, TaskStatus.AUTH_REQUIRED],
        ids=lambda s: s.value,
    )
    def test_a_human_wait_is_never_a_stall(self, status: TaskStatus) -> None:
        """Replanning would discard the question rather than answer it."""
        items = (_item(task_status=TaskStatus.FAILED), _item(task_status=status))
        assert stall_reason(items) is None

    def test_an_undispatched_item_is_not_a_stall(self) -> None:
        """Dispatch writes EXECUTING before it creates the task rows.

        An item whose task has not landed yet is indistinguishable from one
        that never will, so treating it as dead would replan every initiative
        during its own dispatch window.
        """
        items = (_item(task_status=TaskStatus.FAILED), _item(task_status=None))
        assert stall_reason(items) is None

    def test_an_unresolved_decision_is_not_a_stall(self) -> None:
        """The operator owes the choice; a replan would throw it away."""
        items = (
            _item(task_status=TaskStatus.FAILED),
            _item(kind=PlanItemKind.DECISION, chosen_option_id=None),
        )
        assert stall_reason(items) is None

    def test_a_finished_plan_is_not_stalled(self) -> None:
        items = (
            _item(task_status=TaskStatus.COMPLETED),
            _item(kind=PlanItemKind.DECISION, chosen_option_id="opt-a"),
        )
        assert stall_reason(items) is None

    def test_an_itemless_plan_is_not_stalled(self) -> None:
        assert stall_reason(()) is None


class TestDerivePlanStatus:
    """Every item done opens the tail; only the tail's gates deliver."""

    def test_all_items_done_opens_the_tail(self) -> None:
        items = (
            _item(task_status=TaskStatus.COMPLETED),
            _item(kind=PlanItemKind.DECISION, chosen_option_id="opt-a"),
        )
        assert derive_plan_status(items, current=PlanStatus.EXECUTING) is (
            PlanStatus.INTEGRATING
        )

    def test_a_derivation_never_completes_a_plan(self) -> None:
        """Delivery is the evaluate stage's verdict, never a count of items."""
        items = (_item(task_status=TaskStatus.COMPLETED),)
        for current in (
            PlanStatus.EXECUTING,
            PlanStatus.INTEGRATING,
            PlanStatus.EVALUATING,
        ):
            assert derive_plan_status(items, current=current) is not (
                PlanStatus.COMPLETED
            )

    @pytest.mark.parametrize("current", sorted(TAIL_STATUSES), ids=lambda s: s.value)
    def test_a_tail_stage_holds_while_its_items_hold(self, current: PlanStatus) -> None:
        """Only the stage's own gate advances it, so the derivation stands off."""
        items = (_item(task_status=TaskStatus.COMPLETED),)
        assert derive_plan_status(items, current=current) is current

    @pytest.mark.parametrize("current", sorted(TAIL_STATUSES), ids=lambda s: s.value)
    def test_a_regressed_item_reopens_the_build(self, current: PlanStatus) -> None:
        """Integration findings routed back as rework need no replan."""
        items = (
            _item(task_status=TaskStatus.COMPLETED),
            _item(task_status=TaskStatus.IN_PROGRESS),
        )
        assert derive_plan_status(items, current=current) is PlanStatus.EXECUTING

    def test_one_unverified_item_holds_the_plan_executing(self) -> None:
        items = (
            _item(task_status=TaskStatus.COMPLETED),
            _item(task_status=TaskStatus.IN_REVIEW),
        )
        assert derive_plan_status(items, current=PlanStatus.EXECUTING) is (
            PlanStatus.EXECUTING
        )

    def test_one_failed_item_holds_the_plan_executing(self) -> None:
        """Failure is a derived count, never a lifecycle state."""
        items = (
            _item(task_status=TaskStatus.COMPLETED),
            _item(task_status=TaskStatus.FAILED),
        )
        assert derive_plan_status(items, current=PlanStatus.EXECUTING) is (
            PlanStatus.EXECUTING
        )

    def test_unresolved_decision_holds_the_plan_executing(self) -> None:
        items = (
            _item(task_status=TaskStatus.COMPLETED),
            _item(kind=PlanItemKind.DECISION, chosen_option_id=None),
        )
        assert derive_plan_status(items, current=PlanStatus.EXECUTING) is (
            PlanStatus.EXECUTING
        )

    def test_an_itemless_plan_does_not_complete(self) -> None:
        """An empty plan has delivered nothing; it must not self-complete."""
        assert derive_plan_status((), current=PlanStatus.EXECUTING) is (
            PlanStatus.EXECUTING
        )

    def test_a_terminal_plan_is_left_alone(self) -> None:
        items = (_item(task_status=TaskStatus.COMPLETED),)
        for terminal in (
            PlanStatus.COMPLETED,
            PlanStatus.SUPERSEDED,
            PlanStatus.REJECTED,
            PlanStatus.FAILED,
        ):
            assert derive_plan_status(items, current=terminal) is terminal


class TestDeriveProjectStatus:
    """A project follows its plan, and never auto-fails."""

    def test_completed_plan_completes_the_project(self) -> None:
        assert (
            derive_project_status(PlanStatus.COMPLETED, current=ProjectStatus.ACTIVE)
            is ProjectStatus.COMPLETED
        )

    def test_executing_plan_keeps_the_project_active(self) -> None:
        assert (
            derive_project_status(PlanStatus.EXECUTING, current=ProjectStatus.ACTIVE)
            is ProjectStatus.ACTIVE
        )

    @pytest.mark.parametrize(
        ("plan_status", "expected"),
        [
            (PlanStatus.INTEGRATING, ProjectStatus.INTEGRATING),
            (PlanStatus.EVALUATING, ProjectStatus.EVALUATING),
        ],
        ids=lambda s: s.value,
    )
    def test_the_tail_mirrors_onto_the_project(
        self, plan_status: PlanStatus, expected: ProjectStatus
    ) -> None:
        """The cockpit distinguishes building from assembling from scoring."""
        assert (
            derive_project_status(plan_status, current=ProjectStatus.ACTIVE) is expected
        )

    def test_an_undispatched_plan_leaves_the_project_alone(self) -> None:
        assert (
            derive_project_status(
                PlanStatus.PENDING_REVIEW, current=ProjectStatus.PLANNING
            )
            is ProjectStatus.PLANNING
        )

    def test_on_hold_project_is_not_completed_by_the_rollup(self) -> None:
        """An operator hold outranks the rollup: resume before finishing."""
        assert (
            derive_project_status(PlanStatus.COMPLETED, current=ProjectStatus.ON_HOLD)
            is ProjectStatus.ON_HOLD
        )

    @pytest.mark.parametrize(
        "terminal",
        [ProjectStatus.COMPLETED, ProjectStatus.CANCELLED],
        ids=lambda s: s.value,
    )
    def test_a_terminal_project_is_left_alone(self, terminal: ProjectStatus) -> None:
        assert derive_project_status(PlanStatus.COMPLETED, current=terminal) is (
            terminal
        )
