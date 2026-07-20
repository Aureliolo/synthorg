"""Tests for the initiative completion rule (plan item done-ness)."""

import pytest

from synthorg.core.plan_enums import PlanItemKind, PlanStatus
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.initiative.completion import (
    ItemProgress,
    derive_plan_status,
    derive_project_status,
    item_is_done,
    summarise_progress,
)
from tests._shared import as_uuid


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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
class TestDerivePlanStatus:
    """A plan completes only when every item is genuinely done."""

    def test_all_items_done_completes_the_plan(self) -> None:
        items = (
            _item(task_status=TaskStatus.COMPLETED),
            _item(kind=PlanItemKind.DECISION, chosen_option_id="opt-a"),
        )
        assert derive_plan_status(items, current=PlanStatus.EXECUTING) is (
            PlanStatus.COMPLETED
        )

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


@pytest.mark.unit
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
