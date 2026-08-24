"""Unit tests for the sprint recovery sweep.

Each case is a sprint some process stopped halfway through moving. The
sweep's job is that none of them can stay there, and that a sprint nobody
stopped is left exactly where it is.
"""

from typing import override

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.sprint_recovery import SprintRecoveryReconciler
from synthorg.persistence.sprint_protocol import SprintFilterSpec
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_START = "2026-04-01T09:00:00+00:00"
_END = "2026-04-15T09:00:00+00:00"


class _FakeSprintRepo:
    """In-memory store honouring the compare-and-set the sweep relies on."""

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
            if (filter_spec.project is None or s.project == filter_spec.project)
            and (not filter_spec.org_wide_only or s.project is None)
            and (filter_spec.status is None or s.status is filter_spec.status)
        ]
        return tuple(rows[offset : offset + limit])

    async def count(self, filter_spec: SprintFilterSpec) -> int:
        return len(await self.query(filter_spec, limit=1_000_000))

    async def complete_task_if(
        self, sprint_id: str, task_id: str, story_points: float
    ) -> Sprint | None:
        """Present for protocol conformance; the sweep records no deliveries.

        Returns:
            Always ``None``. The sweep advances lifecycle state and must
            never invent a delivery, so a call reaching here should not
            succeed.
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


class _RefusesEveryHop(_FakeSprintRepo):
    """Every CAS is lost, as it is when another process is already driving."""

    @override
    async def transition_if(
        self,
        entity_id: str,
        from_state: SprintStatus,
        to_state: SprintStatus,
        **updates: object,
    ) -> bool:
        return False


class _UnreadableStatus(_FakeSprintRepo):
    """Raises while advancing, to prove one bad sprint does not stop the pass."""

    def __init__(self, *rows: Sprint, failing_id: str) -> None:
        super().__init__(*rows)
        self._failing_id = failing_id

    @override
    async def transition_if(
        self,
        entity_id: str,
        from_state: SprintStatus,
        to_state: SprintStatus,
        **updates: object,
    ) -> bool:
        if entity_id == self._failing_id:
            msg = "store unreachable for this row"
            raise TimeoutError(msg)
        return await super().transition_if(entity_id, from_state, to_state, **updates)


def _sprint(
    sprint_id: str,
    *,
    status: SprintStatus,
    project: str | None = "proj-1",
    task_ids: tuple[str, ...] = ("t-1",),
    completed: tuple[str, ...] = ("t-1",),
    number: int = 1,
) -> Sprint:
    """Build a sprint at *status* with the given delivery state.

    Returns:
        The sprint, carrying the dates its status requires.
    """
    return Sprint(
        id=NotBlankStr(sprint_id),
        project=NotBlankStr(project) if project is not None else None,
        name=NotBlankStr(f"Sprint {number}"),
        sprint_number=number,
        status=status,
        start_date=None if status is SprintStatus.PLANNING else _START,
        end_date=_END if status is SprintStatus.COMPLETED else None,
        task_ids=tuple(NotBlankStr(t) for t in task_ids),
        completed_task_ids=tuple(NotBlankStr(t) for t in completed),
        task_points=dict.fromkeys(task_ids, 1.0),
        story_points_committed=float(len(task_ids)),
        story_points_completed=float(len(completed)),
    )


def _reconciler(
    repo: _FakeSprintRepo, *, active: bool = True
) -> SprintRecoveryReconciler:
    """Build a reconciler over *repo*.

    Returns:
        The reconciler under test.
    """

    async def _active() -> bool:
        return active

    return SprintRecoveryReconciler(
        sprints=repo, sprints_active=_active, clock=FakeClock()
    )


class TestSprintsNotActive:
    async def test_non_agile_org_does_nothing(self) -> None:
        # The store is full of sprints that WOULD be advanced; the org just
        # does not run sprints, so none of them are touched.
        repo = _FakeSprintRepo(_sprint("s-1", status=SprintStatus.ACTIVE))
        report = await _reconciler(repo, active=False).reconcile(trigger="boot")
        assert report.examined == 0
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.ACTIVE


class TestStatusCoverage:
    """One case per non-terminal status: a status with no answer is the defect."""

    async def test_planning_with_a_backlog_is_activated(self) -> None:
        # The activation CAS that follows creation was lost, so nothing
        # activated this sprint and, under the one-open-per-scope index, it
        # blocks its project for ever.
        repo = _FakeSprintRepo(_sprint("s-1", status=SprintStatus.PLANNING))
        report = await _reconciler(repo).reconcile(trigger="periodic")
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.ACTIVE
        assert stored.start_date is not None
        assert report.activated == 1

    async def test_planning_without_a_backlog_is_left_alone(self) -> None:
        # An empty PLANNING sprint is the REST create_sprint product waiting
        # on an operator's add_task. Activating it would take their decision
        # and immediately "deliver" an empty backlog.
        repo = _FakeSprintRepo(
            _sprint("s-1", status=SprintStatus.PLANNING, task_ids=(), completed=())
        )
        report = await _reconciler(repo).reconcile(trigger="periodic")
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.PLANNING
        assert report.unchanged == 1

    async def test_delivered_active_sprint_is_walked_to_completed(self) -> None:
        repo = _FakeSprintRepo(_sprint("s-1", status=SprintStatus.ACTIVE))
        report = await _reconciler(repo).reconcile(trigger="boot")
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.COMPLETED
        assert report.advanced == 1

    async def test_undelivered_active_sprint_is_left_alone(self) -> None:
        repo = _FakeSprintRepo(
            _sprint(
                "s-1",
                status=SprintStatus.ACTIVE,
                task_ids=("t-1", "t-2"),
                completed=("t-1",),
            )
        )
        report = await _reconciler(repo).reconcile(trigger="boot")
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.ACTIVE
        assert report.unchanged == 1

    async def test_delivered_in_review_sprint_is_completed(self) -> None:
        repo = _FakeSprintRepo(_sprint("s-1", status=SprintStatus.IN_REVIEW))
        await _reconciler(repo).reconcile(trigger="boot")
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.COMPLETED

    async def test_retrospective_sprint_is_completed(self) -> None:
        # The shared tail walk starts at IN_REVIEW, so this state needs its
        # own hop: it is what a drain timeout between the two finalise hops
        # leaves behind, and nothing else in the product moves it.
        repo = _FakeSprintRepo(_sprint("s-1", status=SprintStatus.RETROSPECTIVE))
        report = await _reconciler(repo).reconcile(trigger="boot")
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.COMPLETED
        assert stored.end_date is not None
        assert report.advanced == 1

    async def test_completed_sprints_are_not_examined(self) -> None:
        repo = _FakeSprintRepo(
            _sprint("s-1", status=SprintStatus.COMPLETED, completed=("t-1",))
        )
        report = await _reconciler(repo).reconcile(trigger="boot")
        assert report.examined == 0


class TestPassBehaviour:
    async def test_second_pass_changes_nothing(self) -> None:
        repo = _FakeSprintRepo(_sprint("s-1", status=SprintStatus.ACTIVE))
        await _reconciler(repo).reconcile(trigger="boot")
        second = await _reconciler(repo).reconcile(trigger="periodic")
        assert second.examined == 0
        assert second.advanced == 0

    async def test_lost_cas_leaves_the_sprint_to_the_other_writer(self) -> None:
        repo = _RefusesEveryHop(_sprint("s-1", status=SprintStatus.ACTIVE))
        report = await _reconciler(repo).reconcile(trigger="periodic")
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.ACTIVE
        assert report.unchanged == 1
        assert report.failed == 0

    async def test_one_unreadable_sprint_does_not_stop_the_pass(self) -> None:
        repo = _UnreadableStatus(
            _sprint("bad", status=SprintStatus.ACTIVE, number=1),
            _sprint("good", status=SprintStatus.ACTIVE, project="proj-2", number=1),
            failing_id="bad",
        )
        report = await _reconciler(repo).reconcile(trigger="periodic")
        assert report.failed == 1
        good = await repo.get("good")
        assert good is not None
        assert good.status is SprintStatus.COMPLETED

    async def test_org_wide_sprints_are_swept_too(self) -> None:
        repo = _FakeSprintRepo(_sprint("org", status=SprintStatus.ACTIVE, project=None))
        await _reconciler(repo).reconcile(trigger="boot")
        stored = await repo.get("org")
        assert stored is not None
        assert stored.status is SprintStatus.COMPLETED
