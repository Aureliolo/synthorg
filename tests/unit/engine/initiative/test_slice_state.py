"""Tests for the workstream-needs-a-slice derivation."""

import pytest

from synthorg.core.plan import PlanItem
from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.plan_tree import PlanTree
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.completion import ItemProgress
from synthorg.engine.initiative.slice_state import (
    leaf_needs_slice,
    workstream_needs_slice,
    workstream_slice_generation,
)
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit


def _plan_item(
    label: str,
    *,
    parent_id: str | None = None,
    unsplit_reason: str | None = None,
    satisfies: tuple[str, ...] = (),
) -> PlanItem:
    return PlanItem(
        id=sid(label),
        parent_id=parent_id,
        title=NotBlankStr(f"Item {label}"),
        description=NotBlankStr("Do the thing"),
        acceptance_criteria=(NotBlankStr("it is done"),),
        expected_artifacts=(NotBlankStr("src/thing.py"),),
        satisfies=tuple(NotBlankStr(s) for s in satisfies),
        unsplit_reason=NotBlankStr(unsplit_reason) if unsplit_reason else None,
    )


def _progress(*, task_status: TaskStatus | None) -> ItemProgress:
    return ItemProgress(
        item_id=as_uuid("dispatched-task"),
        kind=PlanItemKind.WORK,
        task_id=as_uuid("dispatched-task") if task_status is not None else None,
        task_status=task_status,
    )


class TestLeafNeedsSlice:
    """A completed leaf still carrying ``unsplit_reason`` may be under-covered."""

    def test_completed_and_unsplit_needs_a_slice(self) -> None:
        item = _plan_item("leaf", unsplit_reason="still oversized")
        assert leaf_needs_slice(item, _progress(task_status=TaskStatus.COMPLETED)) is (
            True
        )

    def test_completed_and_atomic_needs_nothing(self) -> None:
        item = _plan_item("leaf")
        assert (
            leaf_needs_slice(item, _progress(task_status=TaskStatus.COMPLETED)) is False
        )

    def test_unsplit_but_not_yet_completed_needs_nothing_yet(self) -> None:
        item = _plan_item("leaf", unsplit_reason="still oversized")
        assert (
            leaf_needs_slice(item, _progress(task_status=TaskStatus.IN_PROGRESS))
            is False
        )


class TestWorkstreamNeedsSlice:
    """A workstream needs a slice once its subtree is done but incomplete.

    Every descendant has finished, but at least one leaf was still oversized
    when a backstop stopped its split.
    """

    def test_a_live_descendant_is_too_early_to_ask(self) -> None:
        workstream = _plan_item("ws")
        leaf = _plan_item("leaf", parent_id=workstream.id, unsplit_reason="oversized")
        tree = PlanTree.of((workstream, leaf))
        progress = {
            workstream.id: _progress(task_status=TaskStatus.COMPLETED),
            leaf.id: _progress(task_status=TaskStatus.IN_PROGRESS),
        }
        assert workstream_needs_slice(
            (workstream, leaf), tree, workstream, progress
        ) == (())

    def test_a_dead_descendant_is_a_stall_not_a_slice(self) -> None:
        """A genuine stall takes the existing stall route, not this one."""
        workstream = _plan_item("ws")
        leaf = _plan_item("leaf", parent_id=workstream.id, unsplit_reason="oversized")
        tree = PlanTree.of((workstream, leaf))
        progress = {
            workstream.id: _progress(task_status=TaskStatus.COMPLETED),
            leaf.id: _progress(task_status=TaskStatus.FAILED),
        }
        assert workstream_needs_slice(
            (workstream, leaf), tree, workstream, progress
        ) == (())

    def test_all_done_and_atomic_needs_nothing(self) -> None:
        workstream = _plan_item("ws")
        leaf = _plan_item("leaf", parent_id=workstream.id)
        tree = PlanTree.of((workstream, leaf))
        progress = {
            workstream.id: _progress(task_status=TaskStatus.COMPLETED),
            leaf.id: _progress(task_status=TaskStatus.COMPLETED),
        }
        assert workstream_needs_slice(
            (workstream, leaf), tree, workstream, progress
        ) == (())

    def test_all_done_but_one_leaf_still_unsplit_needs_a_slice(self) -> None:
        workstream = _plan_item("ws")
        atomic_leaf = _plan_item("atomic", parent_id=workstream.id)
        oversized_leaf = _plan_item(
            "oversized", parent_id=workstream.id, unsplit_reason="depth backstop"
        )
        tree = PlanTree.of((workstream, atomic_leaf, oversized_leaf))
        progress = {
            workstream.id: _progress(task_status=TaskStatus.COMPLETED),
            atomic_leaf.id: _progress(task_status=TaskStatus.COMPLETED),
            oversized_leaf.id: _progress(task_status=TaskStatus.COMPLETED),
        }
        items = (workstream, atomic_leaf, oversized_leaf)
        assert workstream_needs_slice(items, tree, workstream, progress) == (
            (oversized_leaf,)
        )

    def test_a_container_itself_never_counts_as_a_needing_leaf(self) -> None:
        """Only leaves are dispatched atomically; a container is never one."""
        workstream = _plan_item("ws")
        container = _plan_item(
            "mid", parent_id=workstream.id, unsplit_reason="depth backstop"
        )
        leaf = _plan_item("leaf", parent_id=container.id)
        tree = PlanTree.of((workstream, container, leaf))
        progress = {
            workstream.id: _progress(task_status=TaskStatus.COMPLETED),
            container.id: _progress(task_status=TaskStatus.COMPLETED),
            leaf.id: _progress(task_status=TaskStatus.COMPLETED),
        }
        items = (workstream, container, leaf)
        assert workstream_needs_slice(items, tree, workstream, progress) == ()

    def test_an_item_with_no_progress_yet_is_not_done(self) -> None:
        workstream = _plan_item("ws")
        leaf = _plan_item("leaf", parent_id=workstream.id, unsplit_reason="oversized")
        tree = PlanTree.of((workstream, leaf))
        progress = {workstream.id: _progress(task_status=TaskStatus.COMPLETED)}
        assert workstream_needs_slice(
            (workstream, leaf), tree, workstream, progress
        ) == (())


class TestWorkstreamSliceGeneration:
    """The generation is derived from sliced (container-and-unsplit) leaves."""

    def test_a_freshly_planned_workstream_has_generation_zero(self) -> None:
        workstream = _plan_item("ws")
        leaf = _plan_item("leaf", parent_id=workstream.id)
        tree = PlanTree.of((workstream, leaf))
        assert workstream_slice_generation((workstream, leaf), tree, workstream) == 0

    def test_an_unsplit_leaf_with_no_children_yet_does_not_count(self) -> None:
        """Not sliced yet: it is still a leaf, only a candidate for one."""
        workstream = _plan_item("ws")
        leaf = _plan_item("leaf", parent_id=workstream.id, unsplit_reason="oversized")
        tree = PlanTree.of((workstream, leaf))
        assert workstream_slice_generation((workstream, leaf), tree, workstream) == 0

    def test_a_sliced_leaf_that_gained_children_counts_once(self) -> None:
        workstream = _plan_item("ws")
        sliced = _plan_item(
            "sliced", parent_id=workstream.id, unsplit_reason="oversized"
        )
        child = _plan_item("child", parent_id=sliced.id)
        tree = PlanTree.of((workstream, sliced, child))
        items = (workstream, sliced, child)
        assert workstream_slice_generation(items, tree, workstream) == 1

    def test_a_sibling_workstreams_slices_do_not_count(self) -> None:
        workstream = _plan_item("ws")
        other = _plan_item("other-ws")
        sliced = _plan_item("sliced", parent_id=other.id, unsplit_reason="oversized")
        child = _plan_item("child", parent_id=sliced.id)
        tree = PlanTree.of((workstream, other, sliced, child))
        items = (workstream, other, sliced, child)
        assert workstream_slice_generation(items, tree, workstream) == 0
