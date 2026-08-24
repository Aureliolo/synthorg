"""Unit tests for the sprint recovery sweep.

Each case is a sprint some process stopped halfway through moving. The
sweep's job is that none of them can stay there, and that a sprint nobody
stopped is left exactly where it is.
"""

from typing import override

import pytest

from synthorg.core.pagination import MAX_PAGE_SIZE
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.sprint_recovery import SprintRecoveryReconciler
from synthorg.persistence.sprint_protocol import SprintFilterSpec
from tests._shared import FakeClock, FakeSprintRepository

pytestmark = pytest.mark.unit

_START = "2026-04-01T09:00:00+00:00"
_END = "2026-04-15T09:00:00+00:00"


class _RefusesEveryHop(FakeSprintRepository):
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


class _UnreadableStatus(FakeSprintRepository):
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
    repo: FakeSprintRepository, *, active: bool = True
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
        repo = FakeSprintRepository(_sprint("s-1", status=SprintStatus.ACTIVE))
        report = await _reconciler(repo, active=False).reconcile(trigger="boot")
        assert report.examined == 0
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.ACTIVE


class TestStatusCoverage:
    """One case per non-terminal status: a status with no answer is the defect."""

    async def test_planning_sprint_is_left_to_its_operator(self) -> None:
        # PLANNING is only ever reached through REST create_sprint, which
        # hands the operator a shell to fill and then start. Auto-creation
        # inserts an already-ACTIVE sprint, so no lost event can strand one
        # here and there is nothing for the sweep to re-drive.
        repo = FakeSprintRepository(_sprint("s-1", status=SprintStatus.PLANNING))
        report = await _reconciler(repo).reconcile(trigger="periodic")
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.PLANNING
        assert report.waiting == 1

    async def test_delivered_planning_sprint_is_still_left_alone(self) -> None:
        # Even fully "delivered" on paper: a PLANNING sprint has not started,
        # so its backlog cannot have been worked and advancing it would end a
        # sprint that never ran.
        repo = FakeSprintRepository(
            _sprint(
                "s-1",
                status=SprintStatus.PLANNING,
                task_ids=("t-1", "t-2"),
                completed=("t-1", "t-2"),
            )
        )
        report = await _reconciler(repo).reconcile(trigger="periodic")
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.PLANNING
        assert report.waiting == 1

    async def test_delivered_active_sprint_is_walked_to_completed(self) -> None:
        repo = FakeSprintRepository(_sprint("s-1", status=SprintStatus.ACTIVE))
        report = await _reconciler(repo).reconcile(trigger="boot")
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.COMPLETED
        assert report.advanced == 1

    async def test_undelivered_active_sprint_is_left_alone(self) -> None:
        repo = FakeSprintRepository(
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
        assert report.waiting == 1

    async def test_delivered_in_review_sprint_is_completed(self) -> None:
        repo = FakeSprintRepository(_sprint("s-1", status=SprintStatus.IN_REVIEW))
        await _reconciler(repo).reconcile(trigger="boot")
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.COMPLETED

    async def test_retrospective_sprint_is_completed(self) -> None:
        # The shared tail walk starts at IN_REVIEW, so this state needs its
        # own hop: it is what a drain timeout between the two finalise hops
        # leaves behind, and nothing else in the product moves it.
        repo = FakeSprintRepository(_sprint("s-1", status=SprintStatus.RETROSPECTIVE))
        report = await _reconciler(repo).reconcile(trigger="boot")
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.COMPLETED
        assert stored.end_date is not None
        assert report.advanced == 1

    async def test_undelivered_retrospective_sprint_is_still_completed(self) -> None:
        # Unconditional on delivery, unlike every earlier hop: an operator can
        # advance a sprint here by hand with work outstanding, the lifecycle
        # refuses to go back, and nothing else takes this exit. A delivery test
        # would leave the row with no reachable terminal at all.
        repo = FakeSprintRepository(
            _sprint(
                "s-1",
                status=SprintStatus.RETROSPECTIVE,
                task_ids=("t-1", "t-2"),
                completed=("t-1",),
            )
        )
        await _reconciler(repo).reconcile(trigger="boot")
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.COMPLETED

    async def test_completed_sprints_are_not_examined(self) -> None:
        repo = FakeSprintRepository(
            _sprint("s-1", status=SprintStatus.COMPLETED, completed=("t-1",))
        )
        report = await _reconciler(repo).reconcile(trigger="boot")
        assert report.examined == 0


class _ReturnsOneSprintUnderTwoStatuses(FakeSprintRepository):
    """Answers every status query with the same sprint.

    The shape a live store produces when another writer advances a sprint
    between two of the collection's per-status queries: it is returned by
    the query for the status it left AND the one it arrived at.
    """

    @override
    async def query(
        self, filter_spec: SprintFilterSpec, *, limit: int = 50, offset: int = 0
    ) -> tuple[Sprint, ...]:
        if filter_spec.after is not None:
            return ()
        stored = self.rows.get("s-1")
        return () if stored is None else (stored,)


class _AdvancesARowBetweenPages(FakeSprintRepository):
    """Completes the newest ACTIVE row after the first page is served.

    The shape offset paging cannot survive: the row leaves the filtered
    set, everything below it shifts up by one, and the next OFFSET steps
    straight over whichever row moved into the vacated slot. That row is
    returned by no page at all, and in this sweep it is by definition the
    stranded sprint nothing else is looking at.
    """

    def __init__(self, *rows: Sprint) -> None:
        super().__init__(*rows)
        self.pages_served = 0

    @override
    async def query(
        self, filter_spec: SprintFilterSpec, *, limit: int = 50, offset: int = 0
    ) -> tuple[Sprint, ...]:
        page = await super().query(filter_spec, limit=limit, offset=offset)
        if filter_spec.status is not SprintStatus.ACTIVE:
            return page
        self.pages_served += 1
        if self.pages_served == 1 and page:
            leaving = page[0]
            self.rows[leaving.id] = leaving.model_copy(
                update={"status": SprintStatus.COMPLETED, "end_date": _END}
            )
        return page


class TestPassBehaviour:
    async def test_a_sprint_seen_under_two_statuses_is_reconciled_once(
        self,
    ) -> None:
        """Collection is keyed by id, so a mid-pass move is not double-counted.

        Reconciled twice, the second attempt loses its compare-and-set and
        lands in ``raced``, which is the field a reader consults to tell a
        contended sweep from a quiet one.
        """
        repo = _ReturnsOneSprintUnderTwoStatuses(
            _sprint("s-1", status=SprintStatus.ACTIVE)
        )

        report = await _reconciler(repo).reconcile(trigger="periodic")

        assert report.examined == 1
        assert report.advanced == 1
        assert report.raced == 0

    async def test_second_pass_changes_nothing(self) -> None:
        repo = FakeSprintRepository(_sprint("s-1", status=SprintStatus.ACTIVE))
        await _reconciler(repo).reconcile(trigger="boot")
        second = await _reconciler(repo).reconcile(trigger="periodic")
        assert second.examined == 0
        assert second.advanced == 0

    async def test_lost_cas_leaves_the_sprint_to_the_other_writer(self) -> None:
        # Counted as RACED, not as waiting: the sprint was owed a hop and
        # somebody else took it. A pass whose sprints are all raced is
        # arriving after the live observer every time, which is worth
        # telling apart from one watching work that is genuinely in flight.
        repo = _RefusesEveryHop(_sprint("s-1", status=SprintStatus.ACTIVE))
        report = await _reconciler(repo).reconcile(trigger="periodic")
        stored = await repo.get("s-1")
        assert stored is not None
        assert stored.status is SprintStatus.ACTIVE
        assert report.raced == 1
        assert report.waiting == 0
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
        repo = FakeSprintRepository(
            _sprint("org", status=SprintStatus.ACTIVE, project=None)
        )
        await _reconciler(repo).reconcile(trigger="boot")
        stored = await repo.get("org")
        assert stored is not None
        assert stored.status is SprintStatus.COMPLETED

    async def test_examined_is_the_sum_of_the_four_outcomes(self) -> None:
        # Derived rather than counted separately, so no pass can report a
        # total that disagrees with what it says it did.
        repo = _UnreadableStatus(
            _sprint("bad", status=SprintStatus.ACTIVE, project="p1"),
            _sprint("moved", status=SprintStatus.ACTIVE, project="p2"),
            _sprint(
                "left",
                status=SprintStatus.ACTIVE,
                project="p3",
                task_ids=("t-1", "t-2"),
                completed=("t-1",),
            ),
            failing_id="bad",
        )
        report = await _reconciler(repo).reconcile(trigger="periodic")
        assert (report.advanced, report.waiting, report.raced, report.failed) == (
            1,
            1,
            0,
            1,
        )
        assert report.examined == 3

    async def test_sprints_past_the_first_page_are_swept(self) -> None:
        # A stranded sprint sitting past one page is exactly the one nothing
        # else is watching, so the sweep pages each status to exhaustion.
        rows = [
            _sprint(
                f"s-{n}",
                status=SprintStatus.ACTIVE,
                project=f"proj-{n}",
                number=n + 1,
            )
            for n in range(MAX_PAGE_SIZE + 3)
        ]
        repo = FakeSprintRepository(*rows)
        report = await _reconciler(repo).reconcile(trigger="periodic")
        assert report.examined == len(rows)
        last = await repo.get(f"s-{len(rows) - 1}")
        assert last is not None
        assert last.status is SprintStatus.COMPLETED

    async def test_a_row_leaving_mid_drain_skips_nothing(self) -> None:
        """The set being paged is the one the live observer is editing.

        Counting rows to find the next page is what breaks: a row that
        leaves the status shifts everything below it up, and the next
        offset steps over whichever row took the vacated slot. Anchored
        to the last row actually seen, nothing can move into a gap the
        cursor has already passed.
        """
        rows = [
            _sprint(
                f"s-{n}",
                status=SprintStatus.ACTIVE,
                project=f"proj-{n}",
                number=n + 1,
            )
            for n in range(MAX_PAGE_SIZE + 3)
        ]
        repo = _AdvancesARowBetweenPages(*rows)

        report = await _reconciler(repo).reconcile(trigger="periodic")

        assert repo.pages_served > 1
        assert report.examined == len(rows)
        for row in rows:
            stored = await repo.get(row.id)
            assert stored is not None
            assert stored.status is SprintStatus.COMPLETED
