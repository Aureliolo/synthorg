"""The sweep is either wired and driving, or down and saying why.

The reconciler decides WHICH plans need rescuing; this module decides whether
anything asks at all, and what happens to a plan it hands to the coordinator.
Both halves are load-bearing for the one promise the subsystem makes, that a
run survives a restart, and neither is reachable from a test of the
reconciler: an absent collaborator, a claim that outlives its drive, and a
scheduler started but never published all live here.

The ordering pinned below is the subsystem's whole point. The boot pass runs
BEFORE the cadence starts, because a restart is exactly when runs are
stranded and waiting out an interval first leaves the board showing work in
flight with nothing behind it for that whole interval.
"""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from synthorg.api.lifecycle_helpers.run_recovery_wiring import (
    drive_plan_waves,
    live_run_ledger_of,
    unwire_run_recovery,
    wire_run_recovery,
)
from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanItemKind, PlanStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.initiative.ports import DriveOutcome
from synthorg.engine.initiative.rollup import ProjectRollupService
from synthorg.engine.run_recovery.scheduler import RunRecoveryScheduler
from synthorg.engine.state import EngineStateSlice
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry import AgentRegistryService
from synthorg.persistence.plan_protocol import PlanRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.task_protocol import TaskRepository
from tests._shared import FakeClock, as_uuid, make_app_state, mock_of
from tests._shared.scripted_provider import make_e2e_identity

pytestmark = pytest.mark.unit


def _task(label: str = "parent-task") -> Task:
    return Task(
        id=as_uuid(label),
        title=f"Objective {label}",
        description=f"A detailed description for {label}",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="beachhead",
        created_by="ceo",
    )


def _plan(parent: Task, *, status: PlanStatus = PlanStatus.EXECUTING) -> Plan:
    now = FakeClock().now()
    return Plan(
        id=as_uuid("plan"),
        objective_id=NotBlankStr("objective-1"),
        objective_title=NotBlankStr("Ship the thing"),
        parent_task_id=NotBlankStr(str(parent.id)),
        project=NotBlankStr(str(as_uuid("project"))),
        project_name=NotBlankStr("Platform"),
        created_at=now,
        updated_at=now,
        status=status,
        items=(
            PlanItem(
                id=NotBlankStr(str(as_uuid("item-1"))),
                title=NotBlankStr("Build it"),
                description=NotBlankStr("A detailed plan item description"),
                kind=PlanItemKind.WORK,
                acceptance_criteria=(NotBlankStr("the thing builds"),),
                expected_artifacts=(NotBlankStr("the built thing"),),
            ),
        ),
    )


def _persistence(*, parent: Task | None) -> Any:  # type: ignore[explicit-any]  # mock ergonomics; see mock_of
    """A backend whose task repository answers with *parent*, or nothing.

    The plan repository answers empty: the boot pass runs for real in every
    wiring test below, and a sweep with nothing to rescue is the case that
    exercises the ordering without dragging the reconciler's own coverage in.
    """
    tasks = mock_of[TaskRepository](
        get=AsyncMock(return_value=parent),
        save_many=AsyncMock(return_value=None),
    )
    plans = mock_of[PlanRepository](list_items=AsyncMock(return_value=()))
    return mock_of[PersistenceBackend](tasks=tasks, plans=plans)


def _wired_state(**overrides: Any) -> AppState:  # type: ignore[explicit-any]  # heterogeneous service injection; see make_app_state
    """An app state carrying every collaborator ``wire_run_recovery`` needs."""
    defaults: dict[str, Any] = {  # type: ignore[explicit-any]  # ditto
        "persistence": _persistence(parent=_task()),
        "task_engine": mock_of[TaskEngine](),
        # A non-empty roster, because `CoordinationContext` refuses an empty
        # one: a resumed wave with nobody to route to is not a run.
        "agent_registry": mock_of[AgentRegistryService](
            list_active=AsyncMock(return_value=(make_e2e_identity(),)),
        ),
    }
    defaults.update(overrides)
    rollup = mock_of[ProjectRollupService](recompute=AsyncMock(return_value=None))
    return make_app_state(
        slices={EngineStateSlice: {"project_rollup_service": rollup}},
        **defaults,
    )


class TestTheLedgerIsOnePerProcess:
    def test_a_second_ask_returns_the_same_ledger(self) -> None:
        # A second ledger defeats the only thing a ledger does: it would let
        # the approval path and the sweep each believe nothing is driving the
        # plan the other is driving.
        app_state = make_app_state()

        first = live_run_ledger_of(app_state)
        second = live_run_ledger_of(app_state)

        assert first is second
        assert app_state.slice(EngineStateSlice).live_run_ledger is first


class TestTheSweepNamesWhyItIsDown:
    """Every decline states its own condition, per the subsystem rule.

    ``GET /subsystems`` exists to answer "why is this not up", so an
    activation that returns without installing its capability and without
    naming the condition leaves the reconciler nothing to report.
    """

    @pytest.mark.parametrize(
        ("absent", "expected"),
        [
            ("persistence", "persistence backend"),
            ("task_engine", "task engine"),
            ("agent_registry", "agent registry"),
        ],
    )
    async def test_an_absent_collaborator_declines_by_name(
        self, absent: str, expected: str
    ) -> None:
        app_state = _wired_state(**{absent: None})

        with pytest.raises(SubsystemDeclinedError, match=expected):
            await wire_run_recovery(app_state)

        assert app_state.slice(EngineStateSlice).run_recovery_scheduler is None

    async def test_an_absent_rollup_declines_by_name(self) -> None:
        # Not parametrised with its siblings: the rollup lives on a slice the
        # keyword map does not cover, so it is unset by omission.
        app_state = make_app_state(
            persistence=_persistence(parent=_task()),
            task_engine=mock_of[TaskEngine](),
            agent_registry=mock_of[AgentRegistryService](),
        )

        with pytest.raises(SubsystemDeclinedError, match="project rollup"):
            await wire_run_recovery(app_state)

        assert app_state.slice(EngineStateSlice).run_recovery_scheduler is None


class TestBootSweepsBeforeTheCadenceStarts:
    async def test_the_boot_pass_runs_and_then_the_scheduler_is_published(
        self,
    ) -> None:
        app_state = _wired_state(persistence=_persistence(parent=_task()))

        await wire_run_recovery(app_state)

        scheduler = app_state.slice(EngineStateSlice).run_recovery_scheduler
        assert scheduler is not None
        await unwire_run_recovery(app_state)

    async def test_wiring_twice_leaves_the_first_scheduler_alone(self) -> None:
        # A re-entered lifespan must not start a second sweep: two would each
        # claim plans the other is driving.
        app_state = _wired_state()
        await wire_run_recovery(app_state)
        first = app_state.slice(EngineStateSlice).run_recovery_scheduler

        await wire_run_recovery(app_state)

        assert app_state.slice(EngineStateSlice).run_recovery_scheduler is first
        await unwire_run_recovery(app_state)

    async def test_a_failed_publish_stops_the_scheduler_it_started(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Otherwise the wire fails while a sweep runs on, driving plans for a
        # subsystem the reconciler reports as down and nothing can stop.
        app_state = _wired_state()
        started: list[RunRecoveryScheduler] = []
        real_wire = app_state.wire

        def _refuse(slice_type: Any, /, **fields: object) -> None:  # type: ignore[explicit-any]  # the real signature is generic over the slice
            if "run_recovery_scheduler" not in fields:
                real_wire(slice_type, **fields)
                return
            scheduler = fields["run_recovery_scheduler"]
            assert isinstance(scheduler, RunRecoveryScheduler)
            started.append(scheduler)
            msg = "slice write refused"
            raise RuntimeError(msg)

        monkeypatch.setattr(app_state, "wire", _refuse)

        with pytest.raises(RuntimeError, match="slice write refused"):
            await wire_run_recovery(app_state)

        assert len(started) == 1
        assert not started[0].is_running


class TestUnwiringAlwaysDropsTheScheduler:
    async def test_a_stop_that_raises_still_clears_the_slice(self) -> None:
        # Leaving a stopped-or-not scheduler published reports the sweep up
        # while a rebuild waits on it.
        app_state = _wired_state()
        await wire_run_recovery(app_state)
        scheduler = app_state.slice(EngineStateSlice).run_recovery_scheduler
        assert scheduler is not None
        scheduler.stop = AsyncMock(side_effect=RuntimeError("stop refused"))  # type: ignore[method-assign]  # deliberate seam

        await unwire_run_recovery(app_state)

        assert app_state.slice(EngineStateSlice).run_recovery_scheduler is None

    async def test_unwiring_what_was_never_wired_is_harmless(self) -> None:
        app_state = make_app_state()

        await unwire_run_recovery(app_state)

        assert app_state.slice(EngineStateSlice).run_recovery_scheduler is None


class TestTheClaimNeverOutlivesItsDrive:
    """A claim held past its drive hides the plan from every later sweep.

    The reconciler skips a plan the ledger says is being driven, so a claim
    taken and not released strands that plan for the life of the process,
    by the exact mechanism meant to rescue it.
    """

    async def test_a_plan_already_being_driven_is_not_driven_twice(self) -> None:
        app_state = _wired_state()
        parent = _task()
        plan = _plan(parent)
        ledger = live_run_ledger_of(app_state)
        ledger.try_claim(str(plan.id))

        # HELD, not REFUSED: the plan is being driven, just not by this call,
        # so a caller must leave it alone rather than route it to a stall.
        assert await drive_plan_waves(app_state, plan) is DriveOutcome.HELD
        assert app_state.plan_dispatch_background_tasks == set()

    async def test_an_absent_coordinator_releases_the_claim_and_says_so(self) -> None:
        # Nothing was dispatched, so nothing will release it later. Holding it
        # would make every subsequent sweep skip a plan nobody is driving, and
        # answering True would have the sweep report a resume that never was.
        parent = _task()
        app_state = _wired_state(persistence=_persistence(parent=parent))

        assert await drive_plan_waves(app_state, _plan(parent)) is DriveOutcome.REFUSED
        assert not live_run_ledger_of(app_state).is_driving(str(as_uuid("plan")))

    async def test_a_missing_objective_task_releases_the_claim_and_says_so(
        self,
    ) -> None:
        app_state = _wired_state(
            persistence=_persistence(parent=None),
            coordinator=mock_of[MultiAgentCoordinator](),
        )

        assert await drive_plan_waves(app_state, _plan(_task())) is DriveOutcome.REFUSED
        assert not live_run_ledger_of(app_state).is_driving(str(as_uuid("plan")))

    async def test_a_dispatched_drive_releases_the_claim_when_it_ends(self) -> None:
        # The claim transfers from the caller to the background drive, which
        # is what lets the caller return while agents run for minutes.
        parent = _task()
        coordinator = mock_of[MultiAgentCoordinator](
            coordinate=AsyncMock(return_value=SimpleNamespace(is_success=True)),
        )
        app_state = _wired_state(
            persistence=_persistence(parent=parent),
            coordinator=coordinator,
        )
        plan = _plan(parent)

        assert await drive_plan_waves(app_state, plan) is DriveOutcome.DRIVING
        assert live_run_ledger_of(app_state).is_driving(str(plan.id))
        await asyncio.gather(*tuple(app_state.plan_dispatch_background_tasks))

        assert not live_run_ledger_of(app_state).is_driving(str(plan.id))

    async def test_a_drive_that_raises_still_releases_and_rolls_up(self) -> None:
        # A resumed run that blew up must not be left EXECUTING with the
        # ledger claiming it: that is the state recovery exists to leave
        # behind, reintroduced by the recovery path itself.
        parent = _task()
        coordinator = mock_of[MultiAgentCoordinator](
            coordinate=AsyncMock(side_effect=RuntimeError("waves blew up")),
        )
        app_state = _wired_state(
            persistence=_persistence(parent=parent),
            coordinator=coordinator,
        )
        plan = _plan(parent)

        await drive_plan_waves(app_state, plan)
        await asyncio.gather(*tuple(app_state.plan_dispatch_background_tasks))

        assert not live_run_ledger_of(app_state).is_driving(str(plan.id))
        rollup = app_state.slice(EngineStateSlice).project_rollup_service
        assert rollup is not None
        recompute = rollup.recompute
        assert isinstance(recompute, AsyncMock)
        recompute.assert_awaited_once_with(plan.id)


class TestOnlyTheAbsentChildRowsAreFiled:
    async def test_an_existing_child_is_not_rewritten(self) -> None:
        # Re-saving one would reset the status of every subtask that had
        # already finished, undoing the run this is trying to rescue.
        parent = _task()
        persistence = _persistence(parent=parent)
        app_state = _wired_state(
            persistence=persistence,
            coordinator=mock_of[MultiAgentCoordinator](
                coordinate=AsyncMock(return_value=SimpleNamespace(is_success=True)),
            ),
        )

        await drive_plan_waves(app_state, _plan(parent))
        await asyncio.gather(*tuple(app_state.plan_dispatch_background_tasks))

        save_many = persistence.tasks.save_many
        assert isinstance(save_many, AsyncMock)
        save_many.assert_not_awaited()

    async def test_an_absent_child_is_filed_once(self) -> None:
        # A process that stopped between approving a plan and writing its tree
        # leaves a plan with no work queryable at all and no route back.
        parent = _task()
        filed: list[UUID] = []

        async def _only_the_parent_exists(task_id: str) -> Task | None:
            return parent if task_id == str(parent.id) else None

        async def _record(children: tuple[Task, ...]) -> None:
            filed.extend(child.id for child in children)

        tasks = mock_of[TaskRepository](
            get=AsyncMock(side_effect=_only_the_parent_exists),
            save_many=AsyncMock(side_effect=_record),
        )
        app_state = _wired_state(
            persistence=mock_of[PersistenceBackend](tasks=tasks),
            coordinator=mock_of[MultiAgentCoordinator](
                coordinate=AsyncMock(return_value=SimpleNamespace(is_success=True)),
            ),
        )

        await drive_plan_waves(app_state, _plan(parent))
        await asyncio.gather(*tuple(app_state.plan_dispatch_background_tasks))

        assert filed == [as_uuid("item-1")]
