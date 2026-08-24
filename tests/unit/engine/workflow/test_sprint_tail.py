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
from synthorg.persistence.sprint_protocol import SprintFilterSpec
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_START = "2026-04-01T09:00:00+00:00"


class _FakeSprintRepo:
    """Minimal in-memory store: the tail only ever calls ``transition_if``."""

    def __init__(self, *rows: Sprint) -> None:
        self._rows: dict[str, Sprint] = {r.id: r for r in rows}

    async def save(self, entity: Sprint) -> None:
        self._rows[entity.id] = entity

    async def get(self, entity_id: str) -> Sprint | None:
        return self._rows.get(entity_id)

    async def delete(self, entity_id: str) -> bool:
        return self._rows.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[Sprint, ...]:
        return tuple(list(self._rows.values())[offset : offset + limit])

    async def query(
        self, filter_spec: SprintFilterSpec, *, limit: int = 50, offset: int = 0
    ) -> tuple[Sprint, ...]:
        rows = [
            s
            for s in self._rows.values()
            if filter_spec.status is None or s.status is filter_spec.status
        ]
        return tuple(rows[offset : offset + limit])

    async def count(self, filter_spec: SprintFilterSpec) -> int:
        return len(await self.query(filter_spec, limit=1_000_000))

    async def complete_task_if(
        self, sprint_id: str, task_id: str, story_points: float
    ) -> Sprint | None:
        """Present for protocol conformance; the tail never records a delivery.

        Returns:
            Always ``None``. A call reaching here would mean the tail had
            started writing deliveries, which is the one thing it must not
            do, so answering "guard did not match" keeps that visible
            rather than quietly succeeding.
        """
        return None

    async def transition_if(
        self,
        entity_id: str,
        from_state: SprintStatus,
        to_state: SprintStatus,
        **updates: object,
    ) -> bool:
        row = self._rows.get(entity_id)
        if row is None or row.status is not from_state:
            return False
        overrides = {k: v for k, v in updates.items() if v is not None}
        self._rows[entity_id] = row.model_copy(update={"status": to_state, **overrides})
        return True


class _LosesHop(_FakeSprintRepo):
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
        repo = _FakeSprintRepo(sprint)
        result = await open_review_if_delivered(sprint, sprints=repo)
        assert result.status is SprintStatus.IN_REVIEW

    async def test_undelivered_sprint_is_left_alone(self) -> None:
        sprint = _sprint(task_ids=("t-1", "t-2"), completed=("t-1",))
        repo = _FakeSprintRepo(sprint)
        result = await open_review_if_delivered(sprint, sprints=repo)
        assert result.status is SprintStatus.ACTIVE

    async def test_non_active_sprint_is_left_alone(self) -> None:
        sprint = _sprint(status=SprintStatus.IN_REVIEW)
        repo = _FakeSprintRepo(sprint)
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
        repo = _FakeSprintRepo(sprint)
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
        # The caller is told nothing moved, but the row really is at
        # RETROSPECTIVE: this is exactly the state the recovery sweep exists
        # to pick up, and reporting COMPLETED here would hide it.
        assert result.status is SprintStatus.IN_REVIEW
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.RETROSPECTIVE


class TestAdvanceTail:
    async def test_delivered_active_sprint_walks_all_the_way(self) -> None:
        sprint = _sprint()
        repo = _FakeSprintRepo(sprint)
        result = await advance_tail(sprint, sprints=repo, clock=FakeClock())
        assert result.status is SprintStatus.COMPLETED

    async def test_is_idempotent(self) -> None:
        sprint = _sprint()
        repo = _FakeSprintRepo(sprint)
        first = await advance_tail(sprint, sprints=repo, clock=FakeClock())
        # Re-running against the original read, as a second caller holding a
        # stale snapshot would.
        second = await advance_tail(sprint, sprints=repo, clock=FakeClock())
        assert first.status is SprintStatus.COMPLETED
        assert second.status is SprintStatus.ACTIVE
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.COMPLETED
