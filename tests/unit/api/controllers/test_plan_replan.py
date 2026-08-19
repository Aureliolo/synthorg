"""Tests for re-planning a dispatched initiative."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.api.controllers._plan_replan import RevisionInputs, replan_initiative
from synthorg.api.lifecycle_helpers.plan_questions import PLAN_ID_METADATA_KEY
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalStatus
from synthorg.approval.plan_review import PLAN_APPROVAL_ACTION_TYPE
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.domain_errors import (
    ConflictError,
    ServiceUnavailableError,
    ValidationError,
)
from synthorg.core.persistence_errors import QueryError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry_protocol import AgentRegistryProtocol
from synthorg.hr.state import HrStateSlice
from synthorg.persistence.plan_protocol import PlanFilterSpec
from tests._shared import as_uuid, mock_of, sid
from tests._shared.app_state import make_app_state
from tests.unit.api.fakes_backend import FakePersistenceBackend
from tests.unit.meta.chief_of_staff.propose_fakes import make_identity

pytestmark = pytest.mark.unit

# A configured mock_of double: typed at the boundary, but assertion helpers
# (assert_awaited, await_args_list) are not on the Protocol it satisfies.
_Configured = Any  # type: ignore[explicit-any]

_PLAN_ID = "plan-1"
_PROJECT = "proj-1"
_ITEM_A = sid("item-a")
_ITEM_B = sid("item-b")


def _item(item_id: str, title: str) -> PlanItem:
    return PlanItem(
        id=NotBlankStr(item_id),
        title=NotBlankStr(title),
        description=NotBlankStr("Do the thing"),
        acceptance_criteria=(NotBlankStr("it is done"),),
        expected_artifacts=(NotBlankStr("src/thing.py"),),
    )


def _plan(status: PlanStatus) -> Plan:
    now = datetime(2026, 7, 19, tzinfo=UTC)
    return Plan(
        id=as_uuid(_PLAN_ID),
        project=NotBlankStr(sid(_PROJECT)),
        project_name=NotBlankStr("Platform"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship it"),
        parent_task_id=NotBlankStr(sid("parent-1")),
        items=(_item(_ITEM_A, "Original A"),),
        status=status,
        created_at=now,
        updated_at=now,
    )


def _task(
    item_id: str,
    status: TaskStatus,
    *,
    unassigned: bool = False,
) -> Task:
    # A CREATED task carries no assignee; anything past it does, unless the
    # caller asks for the shape a task takes when it failed before it was ever
    # assigned.
    assigned = None if unassigned or status is TaskStatus.CREATED else sid("agent-1")
    return Task(
        id=as_uuid(item_id),
        title="Child",
        description="Child work",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=sid(_PROJECT),
        plan_id=as_uuid(_PLAN_ID),
        plan_item_id=as_uuid(item_id),
        created_by="manager",
        assigned_to=assigned,
        status=status,
    )


def _parked_store(state: AppState) -> _Configured:
    """The approval store *state* was seeded with.

    Returns:
        The double, typed as the configured mock so its recorded calls stay
        readable: ``await_args_list`` is not on the Protocol it satisfies.
    """
    store = state.slice(ApprovalStateSlice).store
    assert store is not None
    return store


async def _seed(
    status: PlanStatus = PlanStatus.EXECUTING,
    *tasks: Task,
) -> tuple[AppState, FakePersistenceBackend, _Configured]:
    backend = FakePersistenceBackend()
    await backend.connect()
    await backend.plans.save(_plan(status))
    await backend.projects.save(
        Project(
            id=as_uuid(_PROJECT),
            name=NotBlankStr("Initiative"),
            plan_id=as_uuid(_PLAN_ID),
            status=ProjectStatus.ACTIVE,
        )
    )
    for task in tasks:
        await backend.tasks.save(task)

    async def _get_task(task_id: str) -> Task | None:
        # Mirror the real engine's read-through so terminate_task sees the
        # live persisted status rather than the caller's snapshot.
        return await backend.tasks.get(NotBlankStr(task_id))

    engine = mock_of[TaskEngine](
        transition_task=AsyncMock(return_value=(None, None)),
        get_task=AsyncMock(side_effect=_get_task),
    )
    # A replan validates its revised owners against the live roster, and
    # refuses outright when there is none: no roster means no answer, and
    # accepting the revision would assert one. The items here name no
    # owner, so an empty roster is enough to get past the check.
    registry = mock_of[AgentRegistryProtocol](list_active=AsyncMock(return_value=()))
    state = make_app_state(
        persistence=backend,
        task_engine=engine,
        agent_registry=registry,
        approval_store=mock_of[ApprovalStoreProtocol](add=AsyncMock()),
    )
    return state, backend, engine


_REVISION = RevisionInputs(items=(_item(_ITEM_B, "Revised B"),))


class TestASuccessorCanActuallyBeDecided:
    """PENDING_REVIEW means a person must approve it, so one must be able to.

    A live run replanned a stalled initiative and left the successor at
    PENDING_REVIEW with no ``plan:approve`` row anywhere: the plan page offers
    Rework, Request changes and Delete but no Approve, the approvals queue had
    nothing naming it, and there is no approve route on the plans controller.
    The initiative could not proceed and nothing said so.

    The gate that parks a first-time plan states this rule in its own
    compensation ("without an approval there is no route to approve or reject
    it, so mark the durable plan FAILED"). The replan path opened exactly that
    state and nothing noticed, because the status and the approval were
    written by different owners.
    """

    async def test_the_successor_is_parked_as_an_approval(self) -> None:
        state, _, _ = await _seed()
        store = _parked_store(state)
        existing = _plan(PlanStatus.EXECUTING)

        successor = await replan_initiative(
            state, existing, revision=_REVISION, requested_by="admin"
        )

        parked = [call.args[0] for call in store.add.await_args_list]
        approvals = [
            item for item in parked if item.action_type == PLAN_APPROVAL_ACTION_TYPE
        ]
        assert len(approvals) == 1, (
            "a plan at PENDING_REVIEW with no plan:approve row cannot be "
            "approved from any surface the product has"
        )
        assert approvals[0].status is ApprovalStatus.PENDING
        assert approvals[0].metadata[PLAN_ID_METADATA_KEY] == str(successor.id)

    async def test_the_successor_open_questions_are_parked_too(self) -> None:
        # The predecessor's questions were answered against a plan that no
        # longer exists, so an unparked successor question reaches nobody:
        # the same defect one level down.
        state, _, _ = await _seed()
        store = _parked_store(state)
        asking = _plan(PlanStatus.EXECUTING).model_copy(
            update={"open_questions": (NotBlankStr("Which runtime is allowed?"),)}
        )

        await replan_initiative(state, asking, revision=_REVISION, requested_by="admin")

        parked = [call.args[0] for call in store.add.await_args_list]
        assert any(item.action_type != PLAN_APPROVAL_ACTION_TYPE for item in parked), (
            "the successor's open question was raised for nobody"
        )

    async def test_a_failed_park_fails_the_successor(self) -> None:
        # The gate's own rule, applied to this path: a PENDING_REVIEW plan
        # nobody can decide is worse than a plan that says it failed.
        state, backend, _ = await _seed()
        _parked_store(state).add = AsyncMock(
            side_effect=QueryError("approval store down")
        )

        successor = await replan_initiative(
            state, _plan(PlanStatus.EXECUTING), revision=_REVISION, requested_by="admin"
        )

        persisted = await backend.plans.get(NotBlankStr(str(successor.id)))
        assert persisted is not None
        assert persisted.status is PlanStatus.FAILED


class TestReplan:
    async def test_retires_the_current_plan_and_opens_a_successor(self) -> None:
        state, backend, _ = await _seed()
        existing = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert existing is not None

        successor = await replan_initiative(
            state,
            existing,
            revision=_REVISION,
            requested_by="admin",
        )

        assert successor.id != existing.id
        assert successor.status is PlanStatus.PENDING_REVIEW
        assert [item.title for item in successor.items] == ["Revised B"]
        retired = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert retired is not None
        assert retired.status is PlanStatus.SUPERSEDED

    async def test_the_project_repoints_at_the_successor(self) -> None:
        state, backend, _ = await _seed()
        existing = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert existing is not None

        successor = await replan_initiative(
            state, existing, revision=_REVISION, requested_by="admin"
        )

        project = await backend.projects.get(NotBlankStr(sid(_PROJECT)))
        assert project is not None
        assert project.plan_id == successor.id
        # The initiative is live throughout, it is only being re-scoped.
        assert project.status is ProjectStatus.ACTIVE

    async def test_the_successor_carries_the_objective_forward(self) -> None:
        state, backend, _ = await _seed()
        existing = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert existing is not None

        successor = await replan_initiative(
            state, existing, revision=_REVISION, requested_by="admin"
        )

        assert successor.project == existing.project
        assert successor.objective_id == existing.objective_id
        assert successor.objective_title == existing.objective_title
        # The retired items stay diffable against the revision.
        assert successor.version_history[-1].items == existing.items

    async def test_in_flight_work_from_the_retired_plan_is_cancelled(self) -> None:
        state, _, engine = await _seed(
            PlanStatus.EXECUTING,
            _task(_ITEM_A, TaskStatus.IN_PROGRESS),
        )
        backend_plan = _plan(PlanStatus.EXECUTING)

        await replan_initiative(
            state, backend_plan, revision=_REVISION, requested_by="admin"
        )

        engine.transition_task.assert_awaited()
        targets = [call.args[1] for call in engine.transition_task.await_args_list]
        assert TaskStatus.CANCELLED in targets

    async def test_already_finished_work_is_left_alone(self) -> None:
        state, _, engine = await _seed(
            PlanStatus.EXECUTING,
            _task(_ITEM_A, TaskStatus.COMPLETED),
        )
        backend_plan = _plan(PlanStatus.EXECUTING)

        await replan_initiative(
            state, backend_plan, revision=_REVISION, requested_by="admin"
        )

        engine.transition_task.assert_not_called()

    async def test_a_created_task_is_rejected_not_cancelled(self) -> None:
        """The lifecycle forbids CREATED -> CANCELLED; it is rejected."""
        state, _, engine = await _seed(
            PlanStatus.EXECUTING,
            _task(_ITEM_A, TaskStatus.CREATED),
        )
        backend_plan = _plan(PlanStatus.EXECUTING)

        await replan_initiative(
            state, backend_plan, revision=_REVISION, requested_by="admin"
        )

        targets = [call.args[1] for call in engine.transition_task.await_args_list]
        assert targets == [TaskStatus.REJECTED]

    async def test_a_task_that_failed_before_assignment_cancels_in_one_hop(
        self,
    ) -> None:
        """The teardown no longer detours through a hop the task cannot take.

        Routing abandonment via ASSIGNED assumed the task keeps its assignee.
        A task that failed before it was ever assigned has none, the hop failed
        the Task validator, and the raw error escaped as a 422 that repeated on
        every retry. With the row unresolvable its plan and its project could
        not be deleted either.
        """
        state, _, engine = await _seed(
            PlanStatus.EXECUTING,
            _task(_ITEM_A, TaskStatus.FAILED, unassigned=True),
        )
        backend_plan = _plan(PlanStatus.EXECUTING)

        await replan_initiative(
            state, backend_plan, revision=_REVISION, requested_by="admin"
        )

        targets = [call.args[1] for call in engine.transition_task.await_args_list]
        assert targets == [TaskStatus.CANCELLED]

    async def test_work_finished_during_the_drain_is_skipped(self) -> None:
        """A task terminal in persistence is not driven through a dead transition.

        The teardown snapshots every task before terminating any; a task can
        finish in the interim. terminate_task re-reads live state, so the stale
        IN_PROGRESS snapshot must not force an invalid transition on the row
        that has since completed.
        """
        state, _, engine = await _seed(
            PlanStatus.EXECUTING,
            _task(_ITEM_A, TaskStatus.IN_PROGRESS),
        )
        # The drain snapshots IN_PROGRESS; the read-through inside
        # terminate_task sees the row after it completed mid-drain.
        engine.get_task = AsyncMock(return_value=_task(_ITEM_A, TaskStatus.COMPLETED))
        backend_plan = _plan(PlanStatus.EXECUTING)

        await replan_initiative(
            state, backend_plan, revision=_REVISION, requested_by="admin"
        )

        engine.transition_task.assert_not_called()

    async def test_only_one_live_plan_remains_for_the_project(self) -> None:
        """The invariant the ordering exists to protect."""
        state, backend, _ = await _seed()
        existing = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert existing is not None

        await replan_initiative(
            state, existing, revision=_REVISION, requested_by="admin"
        )

        plans = await backend.plans.query(
            PlanFilterSpec(project=NotBlankStr(sid(_PROJECT))), limit=50
        )
        live = [p for p in plans if p.status is not PlanStatus.SUPERSEDED]
        assert len(live) == 1

    async def test_a_failed_successor_leaves_the_initiative_intact(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A lost successor insert must not strand the initiative.

        The successor is persisted before anything is retired, so a save
        failure leaves *existing* EXECUTING with its work running: the operator
        retries rather than being stuck with a superseded plan and no successor.
        """
        state, backend, engine = await _seed(
            PlanStatus.EXECUTING,
            _task(_ITEM_A, TaskStatus.IN_PROGRESS),
        )
        monkeypatch.setattr(
            backend.plans,
            "save",
            AsyncMock(
                spec=backend.plans.save,
                side_effect=QueryError("successor insert failed"),
            ),
        )
        existing = _plan(PlanStatus.EXECUTING)

        with pytest.raises(QueryError):
            await replan_initiative(
                state, existing, revision=_REVISION, requested_by="admin"
            )

        persisted = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert persisted is not None
        assert persisted.status is PlanStatus.EXECUTING
        engine.transition_task.assert_not_called()

    async def test_a_failed_repoint_keeps_the_project_on_a_live_plan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A repoint failure never dangles the project pointer.

        The successor is persisted, but the repoint fails; the rollback cannot
        confirm the project points away from the successor, so it keeps it
        rather than delete a row the project might name. The project stays on
        the still-live existing plan and no work is cancelled.
        """
        state, backend, engine = await _seed(PlanStatus.EXECUTING)
        monkeypatch.setattr(
            backend.projects,
            "update",
            AsyncMock(
                spec=backend.projects.update,
                side_effect=QueryError("project repoint failed"),
            ),
        )
        existing = _plan(PlanStatus.EXECUTING)

        with pytest.raises(QueryError):
            await replan_initiative(
                state, existing, revision=_REVISION, requested_by="admin"
            )

        await self._assert_project_points_at_live_plan(backend)
        project = await backend.projects.get(NotBlankStr(sid(_PROJECT)))
        assert project is not None
        # The repoint never landed, so the project stays on the existing plan.
        assert project.plan_id == as_uuid(_PLAN_ID)
        engine.transition_task.assert_not_called()

    async def test_a_failed_supersede_rolls_back_to_one_live_plan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A retirement failure after the repoint compensates fully back.

        The project was repointed at the successor and the retirement then
        fails; the rollback restores the pointer to the existing plan and
        deletes the orphan successor, so one live plan remains and no work is
        cancelled.
        """
        state, backend, engine = await _seed(PlanStatus.EXECUTING)
        monkeypatch.setattr(
            backend.plans,
            "update",
            AsyncMock(
                spec=backend.plans.update,
                side_effect=QueryError("supersede failed"),
            ),
        )
        existing = _plan(PlanStatus.EXECUTING)

        with pytest.raises(QueryError):
            await replan_initiative(
                state, existing, revision=_REVISION, requested_by="admin"
            )

        await self._assert_one_live_executing_plan(backend)
        project = await backend.projects.get(NotBlankStr(sid(_PROJECT)))
        assert project is not None
        assert project.plan_id == as_uuid(_PLAN_ID)
        engine.transition_task.assert_not_called()

    async def test_a_failed_rollback_relink_never_dangles_the_pointer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The repoint lands but the rollback relink fails: no dangling FK.

        The project is repointed at the successor, the retirement then fails,
        and the rollback's relink-back also fails. The successor must not be
        deleted while the project still names it, so ``project.plan_id`` keeps
        resolving to a live plan rather than a removed one.
        """
        state, backend, engine = await _seed(PlanStatus.EXECUTING)
        # sync_status fails, so the compensated block rolls back.
        monkeypatch.setattr(
            backend.plans,
            "update",
            AsyncMock(
                spec=backend.plans.update,
                side_effect=QueryError("supersede failed"),
            ),
        )
        # The initial repoint persists for real; only the rollback relink fails.
        real_update = backend.projects.update
        seen = {"calls": 0}
        relink_error = QueryError("rollback relink failed")

        async def _update(
            project: object, *, expected_version: int | None = None
        ) -> None:
            seen["calls"] += 1
            if seen["calls"] >= 2:
                raise relink_error
            await real_update(project, expected_version=expected_version)  # type: ignore[arg-type]

        monkeypatch.setattr(
            backend.projects,
            "update",
            AsyncMock(spec=backend.projects.update, side_effect=_update),
        )
        existing = _plan(PlanStatus.EXECUTING)

        with pytest.raises(QueryError):
            await replan_initiative(
                state, existing, revision=_REVISION, requested_by="admin"
            )

        # The pointer moved to the successor and the rollback could not move it
        # back, so the successor must still exist: no dangling FK.
        await self._assert_project_points_at_live_plan(backend)
        project = await backend.projects.get(NotBlankStr(sid(_PROJECT)))
        assert project is not None
        assert project.plan_id != as_uuid(_PLAN_ID)
        engine.transition_task.assert_not_called()

    async def test_a_failed_cancellation_leaves_a_coherent_graph(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cancellation is past the point of no return: no rollback, but coherent.

        The old plan is durably superseded and the project already names the
        successor before cancellation runs, so a cancellation failure surfaces
        without stranding the project on a dead plan: it points at the live
        successor, with the retired work left for a later sweep.
        """
        state, backend, engine = await _seed(
            PlanStatus.EXECUTING,
            _task(_ITEM_A, TaskStatus.IN_PROGRESS),
        )
        # Configure the existing autospec'd mock rather than replacing it.
        engine.transition_task.side_effect = QueryError("cancellation failed")
        existing = _plan(PlanStatus.EXECUTING)

        with pytest.raises(QueryError):
            await replan_initiative(
                state, existing, revision=_REVISION, requested_by="admin"
            )

        retired = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert retired is not None
        assert retired.status is PlanStatus.SUPERSEDED
        project = await backend.projects.get(NotBlankStr(sid(_PROJECT)))
        assert project is not None
        assert project.plan_id is not None
        assert project.plan_id != as_uuid(_PLAN_ID)

    @staticmethod
    async def _assert_one_live_executing_plan(
        backend: FakePersistenceBackend,
    ) -> None:
        plans = await backend.plans.query(
            PlanFilterSpec(project=NotBlankStr(sid(_PROJECT))), limit=50
        )
        live = [p for p in plans if p.status is not PlanStatus.SUPERSEDED]
        assert len(live) == 1
        assert live[0].id == as_uuid(_PLAN_ID)
        assert live[0].status is PlanStatus.EXECUTING

    @staticmethod
    async def _assert_project_points_at_live_plan(
        backend: FakePersistenceBackend,
    ) -> None:
        project = await backend.projects.get(NotBlankStr(sid(_PROJECT)))
        assert project is not None
        assert project.plan_id is not None
        referenced = await backend.plans.get(NotBlankStr(str(project.plan_id)))
        assert referenced is not None
        assert referenced.status is not PlanStatus.SUPERSEDED

    @pytest.mark.parametrize(
        "status",
        [
            PlanStatus.DRAFT,
            PlanStatus.PENDING_REVIEW,
            PlanStatus.COMPLETED,
            PlanStatus.REJECTED,
        ],
    )
    async def test_a_plan_that_is_not_dispatched_is_refused(
        self, status: PlanStatus
    ) -> None:
        """A plan under review is edited in place; a terminal one is done."""
        state, backend, _ = await _seed(status)
        existing = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert existing is not None

        with pytest.raises(ConflictError):
            await replan_initiative(
                state, existing, revision=_REVISION, requested_by="admin"
            )

        untouched = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert untouched is not None
        assert untouched.status is status

    async def test_an_owner_the_org_cannot_staff_is_refused(self) -> None:
        """A replan enters through here too, so it validates its own owners.

        The automatic trigger calls this function with items no human
        reviewed, so checking in the controller would leave that path free
        to persist a successor nothing can be dispatched to.
        """
        state, backend, _ = await _seed()
        state.wire(
            HrStateSlice,
            agent_registry=mock_of[AgentRegistryProtocol](
                list_active=AsyncMock(
                    return_value=(make_identity(name="Dana", role="Developer"),)
                )
            ),
        )
        existing = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert existing is not None
        owned = _item(_ITEM_B, "Revised B").model_copy(
            update={"owner": NotBlankStr("Chief Imaginary Officer")}
        )

        with pytest.raises(ValidationError):
            await replan_initiative(
                state,
                existing,
                revision=RevisionInputs(items=(owned,)),
                requested_by="admin",
            )

        # Rejected before any write: nothing retired, no orphan successor.
        untouched = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert untouched is not None
        assert untouched.status is PlanStatus.EXECUTING

    async def test_an_unwired_roster_refuses_rather_than_passing(self) -> None:
        """No roster is no answer, and accepting would assert one."""
        state, backend, _ = await _seed()
        state.wire(HrStateSlice, agent_registry=None)
        existing = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert existing is not None

        with pytest.raises(ServiceUnavailableError):
            await replan_initiative(
                state, existing, revision=_REVISION, requested_by="admin"
            )
