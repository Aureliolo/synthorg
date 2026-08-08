"""C0: a plan or project status change leaves a durable actor record.

A plan reaching COMPLETED had no persisted trace of who moved it: the claim
that only the evaluate stage writes it was provable from a container log and
nowhere else. These lock the ledger append onto both audited status writers
and the failure policy that keeps a ledger outage from lying to the caller.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import structlog

from synthorg.api.services.plan_service import PlanService
from synthorg.core.lifecycle_transition import (
    LifecycleEntityKind,
    LifecycleTransition,
)
from synthorg.core.persistence_errors import QueryError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanItemKind, PlanStatus
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.project_writes import advance_project_status
from synthorg.persistence.lifecycle_ledger import LifecycleLedger
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

    async def test_no_ledger_wired_still_writes_the_plan(self) -> None:
        """A construction-phase service has no backend; the write must still land."""
        plan = _plan()
        repo = mock_of[PlanRepository]()
        service = PlanService(repo=repo, clock=FakeClock(start=_NOW))

        decided = await service.sync_status(plan, PlanStatus.APPROVED)

        assert decided.status is PlanStatus.APPROVED

    async def test_a_ledger_failure_does_not_fail_the_transition(self) -> None:
        """The status write already committed; reporting it as failed would lie."""
        plan = _plan()
        broken = mock_of[FakeLifecycleTransitionRepository]()
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


class TestProjectStatusWrites:
    async def test_every_walked_hop_writes_its_own_row(self) -> None:
        """The intermediate hop is the part a log loses; the ledger keeps it."""
        project = Project(
            id=uuid4(),
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
        assert len(transitions.transitions) > 1
        assert all(
            r.entity_kind is LifecycleEntityKind.PROJECT
            for r in transitions.transitions
        )
        assert transitions.transitions[-1].to_status == ProjectStatus.COMPLETED.value


class TestFilter:
    async def test_query_narrows_to_one_entity(self) -> None:
        """``GET /plans/{id}/transitions`` reads one plan, not the whole ledger."""
        from synthorg.persistence.lifecycle_transition_protocol import (
            LifecycleTransitionFilterSpec,
        )

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
