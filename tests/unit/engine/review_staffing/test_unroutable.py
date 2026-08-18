"""Reading the unroutable backlog without skipping a row or re-reading one.

The sweep walks a set the reconciler releases rows from. Offset pagination over
a shrinking set skips a row for every one that leaves behind the window, and the
row it skips is a task nobody will hire for. These pin the keyset walk instead:
each page asks for what comes after the last id it saw.
"""

from dataclasses import dataclass, field
from typing import cast

import pytest

from synthorg.core.task import Task
from synthorg.core.task_enums import (
    UNROUTABLE_ROLE_KEY,
    BlockedReason,
    TaskStatus,
    TaskType,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.review_staffing.unroutable import unroutable_by_role
from synthorg.persistence.task_protocol import TaskFilterSpec, TaskRepository
from tests._shared import as_pk, mock_of

pytestmark = pytest.mark.unit


def _task(label: str, *, role: str | None) -> Task:
    """Build a parked task carrying (or not carrying) a wanted role.

    Returns:
        The task.
    """
    return Task(
        id=as_pk(label),
        title=NotBlankStr(label),
        description="parked",
        type=TaskType.DEVELOPMENT,
        project=NotBlankStr("proj"),
        created_by=NotBlankStr("operator"),
        status=TaskStatus.BLOCKED,
        blocked_reason=BlockedReason.NO_CAPABLE_AGENT,
        metadata={} if role is None else {UNROUTABLE_ROLE_KEY: role},
    )


@dataclass
class _Backlog:
    """The rows a repository would return, ordered by id like the real one.

    Wrapped in ``mock_of[TaskRepository]`` at each call site rather than passed
    directly: typeguard checks the whole protocol on a bare fake, and this
    stands in for one method of it.
    """

    rows: list[Task]
    seen: list[TaskFilterSpec] = field(default_factory=list)
    release_as_read: bool = False

    async def query(
        self,
        filter_spec: TaskFilterSpec,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Task, ...]:
        """Return the ordered page the filter selects.

        Returns:
            The matching tasks, ordered by id, after ``after_id``.
        """
        self.seen.append(filter_spec)
        assert offset == 0, "the sweep pages by keyset, never by offset"
        ordered = sorted(self.rows, key=lambda task: str(task.id))
        after = filter_spec.after_id
        if after is not None:
            ordered = [task for task in ordered if str(task.id) > after]
        page = tuple(ordered[:limit])
        if self.release_as_read and page:
            # A concurrent reconciler pass releasing the row it was handed, the
            # mutation that makes an offset window skip the row behind it.
            self.rows.remove(page[0])
        return page


def _repo(backlog: _Backlog) -> TaskRepository:
    """Wrap a backlog as the repository the sweep reads through.

    Returns:
        A repository whose ``query`` is the backlog's.
    """
    return cast("TaskRepository", mock_of[TaskRepository](query=backlog.query))


class TestKeysetWalk:
    """Each page asks for what follows the last id, never for an offset."""

    async def test_every_row_is_read_exactly_once_across_pages(self) -> None:
        rows = [_task(f"task-{index}", role="Engineer") for index in range(7)]
        backlog = _Backlog(rows=rows)

        by_role, roleless = await unroutable_by_role(
            _repo(backlog), page_size=3, max_pages=10
        )

        assert roleless == 0
        assert len(by_role["Engineer"]) == len(rows)
        assert {str(task.id) for task in by_role["Engineer"]} == {
            str(task.id) for task in rows
        }

    async def test_the_cursor_advances_to_the_last_id_of_each_page(self) -> None:
        rows = [_task(f"task-{index}", role="Engineer") for index in range(5)]
        backlog = _Backlog(rows=rows)

        await unroutable_by_role(_repo(backlog), page_size=2, max_pages=10)

        ordered = sorted(str(task.id) for task in rows)
        # Three reads for five rows at two a page: the short third page ends the
        # walk, so nothing asks for a fourth.
        assert [spec.after_id for spec in backlog.seen] == [
            None,
            ordered[1],
            ordered[3],
        ]

    async def test_a_row_released_behind_the_window_does_not_skip_its_neighbour(
        self,
    ) -> None:
        """The failure an offset walk produces and a keyset walk cannot."""
        rows = [_task(f"task-{index}", role="Engineer") for index in range(4)]
        expected = {str(task.id) for task in rows}
        backlog = _Backlog(rows=rows, release_as_read=True)

        by_role, _ = await unroutable_by_role(_repo(backlog), page_size=2, max_pages=10)

        # Every row is still in hand: nothing slid past the cursor because
        # another row left the set behind it.
        assert {str(task.id) for task in by_role["Engineer"]} == expected


class TestRolelessRowsAreCountedApart:
    """A row naming no role cannot be offered a hire, so it is not a hire's."""

    async def test_a_blank_role_counts_as_roleless(self) -> None:
        backlog = _Backlog(
            rows=[
                _task("named", role="Engineer"),
                _task("blank", role="   "),
                _task("absent", role=None),
            ]
        )

        by_role, roleless = await unroutable_by_role(
            _repo(backlog), page_size=10, max_pages=2
        )

        assert roleless == 2
        assert list(by_role) == ["Engineer"]


class TestThePageCeilingHolds:
    """A growing backlog must not hold one pass open."""

    async def test_the_walk_stops_at_max_pages(self) -> None:
        rows = [_task(f"task-{index}", role="Engineer") for index in range(20)]
        backlog = _Backlog(rows=rows)

        by_role, _ = await unroutable_by_role(_repo(backlog), page_size=2, max_pages=3)

        assert len(backlog.seen) == 3
        assert len(by_role["Engineer"]) == 6
