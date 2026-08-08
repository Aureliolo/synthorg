"""C0: a plan or project status change leaves a durable actor record.

A plan reaching COMPLETED had no persisted trace of who moved it: the claim
that only the evaluate stage writes it was provable from a container log and
nowhere else. These lock the ledger append onto both audited status writers
and the failure policy that keeps a ledger outage from lying to the caller.
"""

from datetime import UTC, datetime

import pytest
import structlog

from synthorg.api.services.plan_service import PlanService
from synthorg.core.domain_errors import ValidationError
from synthorg.core.lifecycle_transition import (
    LifecycleEntityKind,
    LifecycleTransition,
)
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanItemKind, PlanStatus
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.project_transitions import transition_path
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.project_writes import advance_project_status
from synthorg.persistence.lifecycle_ledger import LifecycleLedger
from synthorg.persistence.lifecycle_transition_protocol import (
    LifecycleTransitionFilterSpec,
    LifecycleTransitionRepository,
)
from synthorg.persistence.plan_protocol import PlanRepository
from synthorg.persistence.project_protocol import ProjectRepository
from tests._shared import FakeClock, as_uuid, mock_of
from tests.unit.api.fakes import FakeLifecycleTransitionRepository

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _plan(status: PlanStatus = PlanStatus.PENDING_REVIEW) -> Plan:
    return Plan(
        project=NotBlankStr("proj-1"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship the loop"),
        parent_task_id=NotBlankStr("task-1"),
        created_at=_NOW,
        updated_at=_NOW,
        items=(
            PlanItem(
                id=NotBlankStr(str(as_uuid("item-1"))),
                title=NotBlankStr("Build it"),
                description=NotBlankStr("Build the thing"),
                kind=PlanItemKind.WORK,
                owner=NotBlankStr("engineer"),
                expected_artifacts=(NotBlankStr("the thing"),),
                acceptance_criteria=(NotBlankStr("it works"),),
            ),
        ),
        status=status,
    )


class TestPlanStatusWrites:
    async def test_a_plan_transition_writes_a_ledger_row(self) -> None:
        """The status says where; the row says how it got there and who moved it."""
        plan = _plan()
        transitions = FakeLifecycleTransitionRepository()
        service = PlanService(
            repo=mock_of[PlanRepository](),
            clock=FakeClock(start=_NOW),
            transitions=transitions,
        )

        await service.sync_status(
            plan,
            PlanStatus.APPROVED,
            requested_by="operator-1",
            reason="looks right",
        )

        assert len(transitions.transitions) == 1
        row = transitions.transitions[0]
        assert row.entity_kind is LifecycleEntityKind.PLAN
        assert row.entity_id == str(plan.id)
        assert row.from_status == PlanStatus.PENDING_REVIEW.value
        assert row.to_status == PlanStatus.APPROVED.value
        assert row.requested_by == "operator-1"
        assert row.reason == "looks right"
        assert row.entity_version == plan.version + 1

    async def test_a_successor_records_its_own_first_status(self) -> None:
        """A successor is a new plan entity, so its birth is a transition too.

        Without it the ledger's account of a replanned initiative starts one
        revision late, and the row that says who opened the revision that
        actually completed is missing.
        """
        existing = _plan(status=PlanStatus.EXECUTING)
        transitions = FakeLifecycleTransitionRepository()
        service = PlanService(
            repo=mock_of[PlanRepository](),
            clock=FakeClock(start=_NOW),
            transitions=transitions,
        )

        successor = await service.open_successor(existing, items=existing.items)

        rows = [r for r in transitions.transitions if r.entity_id == str(successor.id)]
        assert len(rows) == 1
        assert rows[0].from_status is None
        assert rows[0].to_status == successor.status.value

    async def test_a_service_cannot_be_built_without_a_ledger(self) -> None:
        """A ledger-less service would move plans nothing durably witnessed."""
        with pytest.raises(TypeError):
            PlanService(  # type: ignore[call-arg]
                repo=mock_of[PlanRepository](), clock=FakeClock(start=_NOW)
            )

    async def test_a_ledger_failure_does_not_fail_the_transition(self) -> None:
        """The status write already committed; reporting it as failed would lie."""
        plan = _plan()
        # Autospecced against the protocol, not the local fake: the protocol
        # is the typed boundary the service actually holds, so a rename there
        # has to break this test rather than pass against a stale double.
        broken = mock_of[LifecycleTransitionRepository]()
        broken.append.side_effect = QueryError("ledger down")
        service = PlanService(
            repo=mock_of[PlanRepository](),
            clock=FakeClock(start=_NOW),
            transitions=broken,
        )

        with structlog.testing.capture_logs() as captured:
            decided = await service.sync_status(plan, PlanStatus.APPROVED)

        assert decided.status is PlanStatus.APPROVED
        errors = [e for e in captured if e.get("log_level") == "error"]
        assert any(e.get("to_status") == PlanStatus.APPROVED.value for e in errors)

    async def test_a_reason_on_a_live_status_is_refused(self) -> None:
        """A live plan cannot carry a failure reason, so it must not be dropped.

        The live branch writes through ``model_copy``, which neither carries
        the reason nor re-runs the validator that forbids it, so accepting the
        call would silently discard what the caller asked to record.
        """
        plan = _plan()
        service = PlanService(
            repo=mock_of[PlanRepository](),
            clock=FakeClock(start=_NOW),
            transitions=FakeLifecycleTransitionRepository(),
        )

        with pytest.raises(ValidationError, match="only valid for a FAILED plan"):
            await service.sync_status(
                plan,
                PlanStatus.APPROVED,
                failure_reason=NotBlankStr("every reviewer errored"),
            )


class TestLedgerDurability:
    """A row that fails to land is retried, and losing rows becomes loud."""

    async def test_a_transient_failure_is_retried_before_it_is_lost(self) -> None:
        """One dropped connection must not cost the ledger a transition."""
        repo = mock_of[LifecycleTransitionRepository]()
        landed: list[LifecycleTransition] = []
        attempts = 0

        async def _flaky(event: LifecycleTransition) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                msg = "connection reset"
                raise QueryError(msg)
            landed.append(event)

        repo.append.side_effect = _flaky
        ledger = LifecycleLedger(repo, clock=FakeClock(start=_NOW))

        await ledger.record_plan(
            plan_id=as_uuid("plan-retry"),
            from_status=PlanStatus.PENDING_REVIEW,
            to_status=PlanStatus.APPROVED,
            entity_version=2,
        )

        assert len(landed) == 1
        assert ledger.consecutive_failures == 0

    async def test_a_permanent_failure_is_not_retried(self) -> None:
        """A row the storage layer refuses will be refused again."""
        repo = mock_of[LifecycleTransitionRepository]()
        repo.append.side_effect = ConstraintViolationError(
            "bad row", constraint="lifecycle_transitions_pkey"
        )
        ledger = LifecycleLedger(repo, clock=FakeClock(start=_NOW))

        with structlog.testing.capture_logs():
            await ledger.record_plan(
                plan_id=as_uuid("plan-bad"),
                from_status=None,
                to_status=PlanStatus.PLANNING,
                entity_version=1,
            )

        assert repo.append.await_count == 1

    async def test_the_lost_row_is_logged_in_full(self) -> None:
        """Reconstructible from the log, or it is simply gone."""
        repo = mock_of[LifecycleTransitionRepository]()
        repo.append.side_effect = ConstraintViolationError(
            "bad row", constraint="lifecycle_transitions_pkey"
        )
        ledger = LifecycleLedger(repo, clock=FakeClock(start=_NOW))

        with structlog.testing.capture_logs() as captured:
            await ledger.record_plan(
                plan_id=as_uuid("plan-lost"),
                from_status=PlanStatus.APPROVED,
                to_status=PlanStatus.EXECUTING,
                entity_version=4,
                requested_by="operator-1",
                reason="dispatching",
            )

        lost = next(e for e in captured if e.get("log_level") == "error")
        # Every column of the row, so it can be replayed by hand. ``id`` and
        # ``occurred_at`` are the two the first version omitted, which made
        # "reconstructible from the log" untrue.
        assert lost["transition_id"]
        assert lost["occurred_at"] == _NOW.isoformat()
        assert lost["entity_id"] == str(as_uuid("plan-lost"))
        assert lost["from_status"] == PlanStatus.APPROVED.value
        assert lost["to_status"] == PlanStatus.EXECUTING.value
        assert lost["entity_version"] == 4
        assert lost["requested_by"] == "operator-1"
        assert lost["reason"] == "dispatching"

    async def test_a_run_of_failures_says_the_ledger_has_stopped_recording(
        self,
    ) -> None:
        """One gap is a blip; a streak means nothing is being recorded."""
        repo = mock_of[LifecycleTransitionRepository]()
        repo.append.side_effect = ConstraintViolationError(
            "bad row", constraint="lifecycle_transitions_pkey"
        )
        ledger = LifecycleLedger(repo, clock=FakeClock(start=_NOW))

        with structlog.testing.capture_logs() as captured:
            for _ in range(3):
                await ledger.record_plan(
                    plan_id=as_uuid("plan-streak"),
                    from_status=None,
                    to_status=PlanStatus.PLANNING,
                    entity_version=1,
                )

        errors = [e for e in captured if e.get("log_level") == "error"]
        assert [e["consecutive_failures"] for e in errors] == [1, 2, 3]
        assert [e["ledger_recording"] for e in errors] == [True, True, False]


class TestProjectStatusWrites:
    async def test_every_walked_hop_writes_its_own_row(self) -> None:
        """The intermediate hop is the part a log loses; the ledger keeps it."""
        project = Project(
            id=as_uuid("proj-walked"),
            name=NotBlankStr("proj-1"),
            description=NotBlankStr("The initiative"),
            status=ProjectStatus.PLANNING,
        )
        stored: list[Project] = [project]

        async def _get(_project_id: str) -> Project:
            return stored[-1]

        async def _update(updated: Project, *, expected_version: int) -> None:
            _ = expected_version
            stored.append(updated)

        repo = mock_of[ProjectRepository]()
        repo.get.side_effect = _get
        repo.update.side_effect = _update
        transitions = FakeLifecycleTransitionRepository()

        advance = await advance_project_status(
            repo,
            project_id=NotBlankStr(str(project.id)),
            target=ProjectStatus.COMPLETED,
            ledger=LifecycleLedger(transitions, clock=FakeClock(start=_NOW)),
        )

        assert advance.project is not None
        assert advance.project.status is ProjectStatus.COMPLETED
        # The exact hop sequence, not merely "more than one row": the whole
        # point is that the intermediate state a log drops is recorded, and a
        # count assertion passes just as happily on the wrong states.
        expected = transition_path(ProjectStatus.PLANNING, ProjectStatus.COMPLETED)
        assert expected is not None
        assert [r.to_status for r in transitions.transitions] == [
            status.value for status in expected
        ]
        assert transitions.transitions[0].from_status == ProjectStatus.PLANNING.value
        assert all(
            r.entity_kind is LifecycleEntityKind.PROJECT
            for r in transitions.transitions
        )
        assert all(r.entity_id == str(project.id) for r in transitions.transitions)


class TestFilter:
    async def test_query_narrows_to_one_entity(self) -> None:
        """``GET /plans/{id}/transitions`` reads one plan, not the whole ledger."""
        repo = FakeLifecycleTransitionRepository()
        for entity_id, kind in (
            ("plan-a", LifecycleEntityKind.PLAN),
            ("plan-b", LifecycleEntityKind.PLAN),
            ("plan-a", LifecycleEntityKind.PROJECT),
        ):
            await repo.append(
                LifecycleTransition(
                    entity_kind=kind,
                    entity_id=NotBlankStr(entity_id),
                    to_status=NotBlankStr("executing"),
                    entity_version=1,
                    occurred_at=_NOW,
                )
            )

        rows = await repo.query(
            LifecycleTransitionFilterSpec(
                entity_kind=LifecycleEntityKind.PLAN,
                entity_id=NotBlankStr("plan-a"),
            )
        )

        assert len(rows) == 1
        assert rows[0].entity_id == "plan-a"
