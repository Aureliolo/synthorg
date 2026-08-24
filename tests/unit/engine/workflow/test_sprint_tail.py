"""Unit tests for the shared sprint lifecycle tail.

The tail has two callers (the completion observer and the recovery sweep),
so it is tested once here rather than twice through them. Every case that
matters is about a compare-and-set the caller did not win: that is what a
second process looks like from inside one.
"""

from typing import override

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.sprint_tail import (
    advance_tail,
    backlog_fully_delivered,
    finalize_if_delivered,
    open_review_if_delivered,
)
from tests._shared import FakeClock, FakeSprintRepository

pytestmark = pytest.mark.unit

_START = "2026-04-01T09:00:00+00:00"


class _LosesHop(FakeSprintRepository):
    """Refuses one named hop, as a concurrent writer that got there first does."""

    def __init__(self, *rows: Sprint, frm: SprintStatus, to: SprintStatus) -> None:
        super().__init__(*rows)
        self._frm = frm
        self._to = to

    @override
    async def transition_if(
        self,
        entity_id: str,
        from_state: SprintStatus,
        to_state: SprintStatus,
        **updates: object,
    ) -> bool:
        if from_state is self._frm and to_state is self._to:
            return False
        return await super().transition_if(entity_id, from_state, to_state, **updates)


def _sprint(
    *,
    status: SprintStatus = SprintStatus.ACTIVE,
    task_ids: tuple[str, ...] = ("t-1",),
    completed: tuple[str, ...] = ("t-1",),
) -> Sprint:
    """Build a sprint at *status* with the given delivery state.

    Returns:
        The sprint, carrying the dates its status requires.
    """
    return Sprint(
        id=NotBlankStr("s-1"),
        project=NotBlankStr("proj-1"),
        name=NotBlankStr("Sprint 1"),
        sprint_number=1,
        status=status,
        start_date=_START,
        task_ids=tuple(NotBlankStr(t) for t in task_ids),
        completed_task_ids=tuple(NotBlankStr(t) for t in completed),
        task_points=dict.fromkeys(task_ids, 1.0),
        story_points_committed=float(len(task_ids)),
        story_points_completed=float(len(completed)),
    )


class TestBacklogFullyDelivered:
    def test_empty_backlog_is_not_delivered(self) -> None:
        # Otherwise a sprint would end the moment it was created.
        assert backlog_fully_delivered(_sprint(task_ids=(), completed=())) is False

    def test_partial_delivery_is_not_delivered(self) -> None:
        sprint = _sprint(task_ids=("t-1", "t-2"), completed=("t-1",))
        assert backlog_fully_delivered(sprint) is False

    def test_full_delivery_is_delivered(self) -> None:
        assert backlog_fully_delivered(_sprint()) is True


class TestOpenReviewIfDelivered:
    async def test_delivered_active_sprint_opens_review(self) -> None:
        sprint = _sprint()
        repo = FakeSprintRepository(sprint)
        result = await open_review_if_delivered(sprint, sprints=repo)
        assert result.status is SprintStatus.IN_REVIEW

    async def test_undelivered_sprint_is_left_alone(self) -> None:
        sprint = _sprint(task_ids=("t-1", "t-2"), completed=("t-1",))
        repo = FakeSprintRepository(sprint)
        result = await open_review_if_delivered(sprint, sprints=repo)
        assert result.status is SprintStatus.ACTIVE

    async def test_non_active_sprint_is_left_alone(self) -> None:
        sprint = _sprint(status=SprintStatus.IN_REVIEW)
        repo = FakeSprintRepository(sprint)
        result = await open_review_if_delivered(sprint, sprints=repo)
        assert result.status is SprintStatus.IN_REVIEW

    async def test_lost_cas_returns_the_sprint_unchanged(self) -> None:
        sprint = _sprint()
        repo = _LosesHop(sprint, frm=SprintStatus.ACTIVE, to=SprintStatus.IN_REVIEW)
        result = await open_review_if_delivered(sprint, sprints=repo)
        assert result.status is SprintStatus.ACTIVE


class TestFinalizeIfDelivered:
    async def test_walks_review_to_completed(self) -> None:
        sprint = _sprint(status=SprintStatus.IN_REVIEW)
        repo = FakeSprintRepository(sprint)
        result = await finalize_if_delivered(sprint, sprints=repo, clock=FakeClock())
        assert result.status is SprintStatus.COMPLETED
        assert result.end_date is not None
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.COMPLETED

    async def test_lost_first_hop_stops_before_retrospective(self) -> None:
        sprint = _sprint(status=SprintStatus.IN_REVIEW)
        repo = _LosesHop(
            sprint, frm=SprintStatus.IN_REVIEW, to=SprintStatus.RETROSPECTIVE
        )
        result = await finalize_if_delivered(sprint, sprints=repo, clock=FakeClock())
        assert result.status is SprintStatus.IN_REVIEW

    async def test_lost_second_hop_leaves_the_sprint_in_retrospective(self) -> None:
        sprint = _sprint(status=SprintStatus.IN_REVIEW)
        repo = _LosesHop(
            sprint, frm=SprintStatus.RETROSPECTIVE, to=SprintStatus.COMPLETED
        )
        result = await finalize_if_delivered(sprint, sprints=repo, clock=FakeClock())
        # The first hop landed, so the caller is handed RETROSPECTIVE rather
        # than the IN_REVIEW pre-image: that state no longer exists, and it is
        # exactly what the recovery sweep exists to pick up. Reporting
        # COMPLETED would hide it; reporting IN_REVIEW would misname it.
        assert result.status is SprintStatus.RETROSPECTIVE
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.RETROSPECTIVE


class TestAdvanceTail:
    async def test_delivered_active_sprint_walks_all_the_way(self) -> None:
        sprint = _sprint()
        repo = FakeSprintRepository(sprint)
        result = await advance_tail(sprint, sprints=repo, clock=FakeClock())
        assert result.status is SprintStatus.COMPLETED

    async def test_is_idempotent(self) -> None:
        sprint = _sprint()
        repo = FakeSprintRepository(sprint)
        first = await advance_tail(sprint, sprints=repo, clock=FakeClock())
        # Re-running against the original read, as a second caller holding a
        # stale snapshot would.
        second = await advance_tail(sprint, sprints=repo, clock=FakeClock())
        assert first.status is SprintStatus.COMPLETED
        assert second.status is SprintStatus.ACTIVE
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.COMPLETED
