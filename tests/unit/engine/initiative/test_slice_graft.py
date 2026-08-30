"""Tests for grafting a workstream's next slice onto a live plan."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.core.persistence_errors import PersistenceVersionConflictError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_tree import PlanTree
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStructure, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.initiative.ports import DriveOutcome
from synthorg.engine.initiative.slice_autonomy import EffectiveAutonomyForPlan
from synthorg.engine.initiative.slice_graft import (
    DEFAULT_SLICE_ENABLED,
    DEFAULT_SLICE_MAX_GENERATIONS,
    SliceCollaborators,
    consider_slice,
    graft_slice,
    grant_slice,
    resolve_slice_enabled,
    resolve_slice_max_generations,
)
from synthorg.engine.initiative.slice_state import SliceDisposition
from synthorg.engine.initiative.stage_runner import StageRunner
from synthorg.engine.task_engine import TaskEngine
from synthorg.security.autonomy.enums import ActionType
from synthorg.settings.resolver import ConfigResolver
from tests._shared import FakeClock, as_uuid, mock_of, sid
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit

_PLAN_ID = "plan-1"
_PROJECT = "proj-1"
_WORKSTREAM = sid("ws-1")
_LEAF = sid("leaf-1")


def _item(
    item_id: str,
    *,
    parent_id: str | None = None,
    unsplit_reason: str | None = None,
    satisfies: tuple[str, ...] = (),
) -> PlanItem:
    return PlanItem(
        id=item_id,
        parent_id=parent_id,
        title=NotBlankStr(f"Item {item_id[:4]}"),
        description=NotBlankStr("Do the thing"),
        acceptance_criteria=(NotBlankStr("it is done"),),
        expected_artifacts=(NotBlankStr("src/thing.py"),),
        satisfies=tuple(NotBlankStr(s) for s in satisfies),
        unsplit_reason=NotBlankStr(unsplit_reason) if unsplit_reason else None,
    )


def _plan(*items: PlanItem, version: int = 1) -> Plan:
    now = datetime(2026, 7, 24, tzinfo=UTC)
    return Plan(
        id=as_uuid(_PLAN_ID),
        project=NotBlankStr(sid(_PROJECT)),
        project_name=NotBlankStr("Platform"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship it"),
        parent_task_id=NotBlankStr(sid("parent-1")),
        items=items,
        objective_criteria=(NotBlankStr("the game is playable"),),
        version=version,
        created_at=now,
        updated_at=now,
    )


def _leaf_task() -> Task:
    return Task(
        id=UUID(_LEAF),
        title="Leaf",
        description="Do the leaf's work",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=sid(_PROJECT),
        created_by="manager",
    )


def _decomposition(*subtask_ids: str) -> DecompositionResult:
    subtasks = tuple(
        SubtaskDefinition(
            id=sid(subtask_id),
            title=f"Split {subtask_id}",
            description="A narrower piece of the leaf's claim",
            acceptance_criteria=(NotBlankStr("it works"),),
            expected_artifacts=(NotBlankStr("src/split.py"),),
        )
        for subtask_id in subtask_ids
    )
    return DecompositionResult(
        plan=DecompositionPlan(
            parent_task_id=str(_LEAF),
            subtasks=subtasks,
            task_structure=TaskStructure.SEQUENTIAL,
        ),
        created_tasks=tuple(
            Task(
                id=as_uuid(subtask_id),
                title=f"Split {subtask_id}",
                description="A narrower piece of the leaf's claim",
                type=TaskType.DEVELOPMENT,
                priority=Priority.MEDIUM,
                project=sid(_PROJECT),
                created_by="manager",
                parent_task_id=str(_LEAF),
            )
            for subtask_id in subtask_ids
        ),
    )


async def _no_owner(_plan: Plan) -> AgentIdentity | None:
    return None


async def _no_roster() -> tuple[NotBlankStr, ...]:
    return ()


async def _auto_approve_autonomy(_plan: Plan) -> EffectiveAutonomy | None:
    return EffectiveAutonomy(
        level=AutonomyLevel.FULL,
        auto_approve_actions=frozenset({ActionType.PLAN_EXTEND_WORKSTREAM.value}),
        human_approval_actions=frozenset(),
        security_agent=False,
    )


async def _human_approval_autonomy(_plan: Plan) -> EffectiveAutonomy | None:
    return EffectiveAutonomy(
        level=AutonomyLevel.SUPERVISED,
        auto_approve_actions=frozenset(),
        human_approval_actions=frozenset({ActionType.PLAN_EXTEND_WORKSTREAM.value}),
        security_agent=True,
    )


async def _unresolvable_autonomy(_plan: Plan) -> EffectiveAutonomy | None:
    return None


class TestResolveSliceEnabled:
    """The master switch, live-read with a safe fallback."""

    async def test_no_resolver_returns_the_default(self) -> None:
        assert await resolve_slice_enabled(None) is DEFAULT_SLICE_ENABLED

    async def test_reads_the_live_setting(self) -> None:
        resolver: ConfigResolver = mock_of[ConfigResolver](
            get_bool=AsyncMock(return_value=False)
        )
        assert await resolve_slice_enabled(resolver) is False

    async def test_a_failed_read_falls_back_to_the_default(self) -> None:
        resolver: ConfigResolver = mock_of[ConfigResolver](
            get_bool=AsyncMock(side_effect=RuntimeError("settings down"))
        )
        assert await resolve_slice_enabled(resolver) is DEFAULT_SLICE_ENABLED


class TestResolveSliceMaxGenerations:
    """The per-workstream generation cap, live-read with a safe fallback."""

    async def test_no_resolver_returns_the_default(self) -> None:
        result = await resolve_slice_max_generations(None)
        assert result == DEFAULT_SLICE_MAX_GENERATIONS

    async def test_reads_the_live_setting(self) -> None:
        resolver: ConfigResolver = mock_of[ConfigResolver](
            get_int=AsyncMock(return_value=5)
        )
        assert await resolve_slice_max_generations(resolver) == 5

    async def test_a_failed_read_falls_back_to_the_default(self) -> None:
        resolver: ConfigResolver = mock_of[ConfigResolver](
            get_int=AsyncMock(side_effect=RuntimeError("settings down"))
        )
        result = await resolve_slice_max_generations(resolver)
        assert result == DEFAULT_SLICE_MAX_GENERATIONS


class TestGraftSlice:
    """Decompose the leaf's remaining scope and graft it under the leaf."""

    async def test_the_slice_is_declared_against_the_leafs_own_claim(self) -> None:
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        decompose = AsyncMock(return_value=_decomposition("sub-1"))
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)

        await graft_slice(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            decomposition_service=mock_of[DecompositionService](
                decompose_task=decompose
            ),
            persistence=backend,
            clock=FakeClock(),
            drive=None,
        )

        assert decompose.await_args is not None
        context = decompose.await_args.args[1]
        assert context.objective_criteria == leaf.satisfies
        assert context.current_depth == 0

    async def test_new_items_are_reparented_under_the_leaf(self) -> None:
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)

        updated = await graft_slice(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            decomposition_service=mock_of[DecompositionService](
                decompose_task=AsyncMock(return_value=_decomposition("sub-1", "sub-2"))
            ),
            persistence=backend,
            clock=FakeClock(),
            drive=None,
        )

        assert updated is not None
        new_items = [item for item in updated.items if item.parent_id == leaf.id]
        assert {item.id for item in new_items} == {sid("sub-1"), sid("sub-2")}
        assert updated.version == plan.version + 1

    async def test_the_original_items_survive_byte_for_byte(self) -> None:
        workstream = _item(_WORKSTREAM)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, leaf)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)

        updated = await graft_slice(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            decomposition_service=mock_of[DecompositionService](
                decompose_task=AsyncMock(return_value=_decomposition("sub-1"))
            ),
            persistence=backend,
            clock=FakeClock(),
            drive=None,
        )

        assert updated is not None
        assert workstream in updated.items
        assert leaf in updated.items

    async def test_only_the_new_tasks_are_filed(self) -> None:
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        await backend.tasks.save(_leaf_task())

        await graft_slice(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            decomposition_service=mock_of[DecompositionService](
                decompose_task=AsyncMock(return_value=_decomposition("sub-1"))
            ),
            persistence=backend,
            clock=FakeClock(),
            drive=None,
        )

        filed = await backend.tasks.get(str(sid("sub-1")))
        assert filed is not None
        # The pre-existing leaf task is untouched, never re-saved.
        leaf_row = await backend.tasks.get(str(_LEAF))
        assert leaf_row is not None

    async def test_the_driver_is_called_once_on_a_successful_graft(self) -> None:
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        drive = AsyncMock(return_value=DriveOutcome.DRIVING)

        await graft_slice(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            decomposition_service=mock_of[DecompositionService](
                decompose_task=AsyncMock(return_value=_decomposition("sub-1"))
            ),
            persistence=backend,
            clock=FakeClock(),
            drive=drive,
        )

        assert drive.await_count == 1

    async def test_a_refused_drive_still_returns_the_grafted_plan(self) -> None:
        """The graft itself succeeded; dispatch is a separate concern."""
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        drive = AsyncMock(return_value=DriveOutcome.REFUSED)

        updated = await graft_slice(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            decomposition_service=mock_of[DecompositionService](
                decompose_task=AsyncMock(return_value=_decomposition("sub-1"))
            ),
            persistence=backend,
            clock=FakeClock(),
            drive=drive,
        )

        assert updated is not None

    async def test_a_version_conflict_is_retried_once_and_succeeds(self) -> None:
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        real_update = backend.plans.update
        calls = {"count": 0}

        async def flaky_update(
            candidate: Plan, *, expected_version: int | None = None
        ) -> None:
            calls["count"] += 1
            if calls["count"] == 1:
                # A concurrent writer lands first: bump the stored version,
                # then answer this attempt with the conflict it would see.
                await real_update(
                    plan.model_copy(update={"version": plan.version + 1}),
                    expected_version=plan.version,
                )
                msg = "moved"
                raise PersistenceVersionConflictError(msg)
            await real_update(candidate, expected_version=expected_version)

        backend.plans.update = flaky_update  # type: ignore[method-assign,assignment]

        updated = await graft_slice(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            decomposition_service=mock_of[DecompositionService](
                decompose_task=AsyncMock(return_value=_decomposition("sub-1"))
            ),
            persistence=backend,
            clock=FakeClock(),
            drive=None,
        )

        assert updated is not None
        assert calls["count"] == 2

    async def test_two_version_conflicts_give_up(self) -> None:
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)

        async def always_conflicts(
            candidate: Plan, *, expected_version: int | None = None
        ) -> None:
            del candidate, expected_version
            msg = "moved"
            raise PersistenceVersionConflictError(msg)

        backend.plans.update = always_conflicts  # type: ignore[method-assign,assignment]

        updated = await graft_slice(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            decomposition_service=mock_of[DecompositionService](
                decompose_task=AsyncMock(return_value=_decomposition("sub-1"))
            ),
            persistence=backend,
            clock=FakeClock(),
            drive=None,
        )

        assert updated is None


def _collaborators(
    backend: FakePersistenceBackend,
    *,
    decompose: AsyncMock,
    resolver: ConfigResolver | None = None,
    runner: StageRunner | None = None,
    effective_autonomy: EffectiveAutonomyForPlan | None = None,
) -> SliceCollaborators:
    return SliceCollaborators(
        persistence=backend,
        task_engine=mock_of[TaskEngine](get_task=AsyncMock(return_value=_leaf_task())),
        decomposition_service=mock_of[DecompositionService](decompose_task=decompose),
        config_resolver=resolver
        or mock_of[ConfigResolver](
            get_bool=AsyncMock(return_value=True),
            get_int=AsyncMock(return_value=2),
        ),
        runner=runner
        or StageRunner(
            owner="test.slice",
            clock=FakeClock(),
            skipped_event="test.slice.skipped",
            failed_event="test.slice.failed",
        ),
        owner_resolver=_no_owner,
        roster_resolver=_no_roster,
        effective_autonomy=effective_autonomy or _auto_approve_autonomy,
        clock=FakeClock(),
    )


class TestConsiderSlice:
    """Both refusals are decided before any detached work starts."""

    async def test_disabled_refuses_before_any_decomposition_call(self) -> None:
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        workstream = _item(_WORKSTREAM)
        plan = _plan(workstream, leaf)
        tree = PlanTree.of(plan.items)
        decompose = AsyncMock()
        resolver: ConfigResolver = mock_of[ConfigResolver](
            get_bool=AsyncMock(return_value=False)
        )

        disposition = await consider_slice(
            plan=plan,
            tree=tree,
            workstream=workstream,
            leaf=leaf,
            drive=None,
            collaborators=_collaborators(
                FakePersistenceBackend(), decompose=decompose, resolver=resolver
            ),
        )

        assert disposition is SliceDisposition.DISABLED
        decompose.assert_not_awaited()

    async def test_generation_at_the_cap_is_refused(self) -> None:
        workstream = _item(_WORKSTREAM)
        sliced = _item(sid("sliced"), parent_id=_WORKSTREAM, unsplit_reason="oversized")
        child = _item(sid("child"), parent_id=sliced.id)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, sliced, child, leaf)
        tree = PlanTree.of(plan.items)
        decompose = AsyncMock()
        resolver: ConfigResolver = mock_of[ConfigResolver](
            get_bool=AsyncMock(return_value=True),
            get_int=AsyncMock(return_value=1),
        )

        disposition = await consider_slice(
            plan=plan,
            tree=tree,
            workstream=workstream,
            leaf=leaf,
            drive=None,
            collaborators=_collaborators(
                FakePersistenceBackend(), decompose=decompose, resolver=resolver
            ),
        )

        assert disposition is SliceDisposition.BUDGET_EXHAUSTED
        decompose.assert_not_awaited()

    async def test_a_slice_already_in_flight_collapses_into_it(self) -> None:
        workstream = _item(_WORKSTREAM)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, leaf)
        tree = PlanTree.of(plan.items)
        runner = StageRunner(
            owner="test.slice",
            clock=FakeClock(),
            skipped_event="test.slice.skipped",
            failed_event="test.slice.failed",
        )
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        decompose = AsyncMock(return_value=_decomposition("sub-1"))

        first = await consider_slice(
            plan=plan,
            tree=tree,
            workstream=workstream,
            leaf=leaf,
            drive=None,
            collaborators=_collaborators(backend, decompose=decompose, runner=runner),
        )
        second = await consider_slice(
            plan=plan,
            tree=tree,
            workstream=workstream,
            leaf=leaf,
            drive=None,
            collaborators=_collaborators(backend, decompose=decompose, runner=runner),
        )
        await runner.drain(timeout_sec=5.0)

        assert first is SliceDisposition.GRAFTED
        assert second is SliceDisposition.ALREADY_RUNNING

    async def test_the_happy_path_grafts_the_slice(self) -> None:
        workstream = _item(_WORKSTREAM)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, leaf)
        tree = PlanTree.of(plan.items)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        decompose = AsyncMock(return_value=_decomposition("sub-1"))
        runner = StageRunner(
            owner="test.slice",
            clock=FakeClock(),
            skipped_event="test.slice.skipped",
            failed_event="test.slice.failed",
        )

        disposition = await consider_slice(
            plan=plan,
            tree=tree,
            workstream=workstream,
            leaf=leaf,
            drive=None,
            collaborators=_collaborators(backend, decompose=decompose, runner=runner),
        )
        await runner.drain(timeout_sec=5.0)

        assert disposition is SliceDisposition.GRAFTED
        fresh = await backend.plans.get(str(plan.id))
        assert fresh is not None
        assert any(item.parent_id == leaf.id for item in fresh.items)

    async def test_a_leaf_already_sliced_by_another_writer_is_a_no_op(self) -> None:
        """The re-check inside the detached work catches a stale ask."""
        workstream = _item(_WORKSTREAM)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, leaf)
        tree = PlanTree.of(plan.items)
        backend = FakePersistenceBackend()
        # Another writer already sliced the leaf and cleared its status by
        # the time the detached work actually runs.
        already_sliced_leaf = leaf.model_copy(update={"unsplit_reason": None})
        already_sliced_plan = plan.model_copy(
            update={"items": (workstream, already_sliced_leaf), "version": 2}
        )
        await backend.plans.save(already_sliced_plan)
        decompose = AsyncMock(return_value=_decomposition("sub-1"))
        runner = StageRunner(
            owner="test.slice",
            clock=FakeClock(),
            skipped_event="test.slice.skipped",
            failed_event="test.slice.failed",
        )

        await consider_slice(
            plan=plan,
            tree=tree,
            workstream=workstream,
            leaf=leaf,
            drive=None,
            collaborators=_collaborators(backend, decompose=decompose, runner=runner),
        )
        await runner.drain(timeout_sec=5.0)

        decompose.assert_not_awaited()


class TestConsiderSliceAutonomyGate:
    """The deterministic gate is decided before any detached work starts."""

    async def test_human_approval_required_asks_before_any_decomposition(
        self,
    ) -> None:
        workstream = _item(_WORKSTREAM)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, leaf)
        tree = PlanTree.of(plan.items)
        decompose = AsyncMock()

        disposition = await consider_slice(
            plan=plan,
            tree=tree,
            workstream=workstream,
            leaf=leaf,
            drive=None,
            collaborators=_collaborators(
                FakePersistenceBackend(),
                decompose=decompose,
                effective_autonomy=_human_approval_autonomy,
            ),
        )

        assert disposition is SliceDisposition.ASKED
        decompose.assert_not_awaited()

    async def test_unresolvable_autonomy_fails_closed_to_asked(self) -> None:
        workstream = _item(_WORKSTREAM)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, leaf)
        tree = PlanTree.of(plan.items)
        decompose = AsyncMock()

        disposition = await consider_slice(
            plan=plan,
            tree=tree,
            workstream=workstream,
            leaf=leaf,
            drive=None,
            collaborators=_collaborators(
                FakePersistenceBackend(),
                decompose=decompose,
                effective_autonomy=_unresolvable_autonomy,
            ),
        )

        assert disposition is SliceDisposition.ASKED
        decompose.assert_not_awaited()

    async def test_auto_approved_grafts_without_asking(self) -> None:
        workstream = _item(_WORKSTREAM)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, leaf)
        tree = PlanTree.of(plan.items)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        decompose = AsyncMock(return_value=_decomposition("sub-1"))
        runner = StageRunner(
            owner="test.slice",
            clock=FakeClock(),
            skipped_event="test.slice.skipped",
            failed_event="test.slice.failed",
        )

        disposition = await consider_slice(
            plan=plan,
            tree=tree,
            workstream=workstream,
            leaf=leaf,
            drive=None,
            collaborators=_collaborators(
                backend,
                decompose=decompose,
                runner=runner,
                effective_autonomy=_auto_approve_autonomy,
            ),
        )
        await runner.drain(timeout_sec=5.0)

        assert disposition is SliceDisposition.GRAFTED


class TestGrantSlice:
    """A person's grant bypasses every automatic guard but ALREADY_RUNNING."""

    async def test_grants_past_a_human_approval_gate(self) -> None:
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        decompose = AsyncMock(return_value=_decomposition("sub-1"))
        runner = StageRunner(
            owner="test.slice",
            clock=FakeClock(),
            skipped_event="test.slice.skipped",
            failed_event="test.slice.failed",
        )

        started = await grant_slice(
            plan=plan,
            leaf=leaf,
            drive=None,
            requested_by="an-operator",
            collaborators=_collaborators(
                backend,
                decompose=decompose,
                runner=runner,
                effective_autonomy=_human_approval_autonomy,
            ),
        )
        await runner.drain(timeout_sec=5.0)

        assert started is True
        fresh = await backend.plans.get(str(plan.id))
        assert fresh is not None
        assert any(item.parent_id == leaf.id for item in fresh.items)

    async def test_a_grant_already_in_flight_does_not_start_twice(self) -> None:
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        decompose = AsyncMock(return_value=_decomposition("sub-1"))
        runner = StageRunner(
            owner="test.slice",
            clock=FakeClock(),
            skipped_event="test.slice.skipped",
            failed_event="test.slice.failed",
        )
        collaborators = _collaborators(backend, decompose=decompose, runner=runner)

        first = await grant_slice(
            plan=plan,
            leaf=leaf,
            drive=None,
            requested_by="an-operator",
            collaborators=collaborators,
        )
        second = await grant_slice(
            plan=plan,
            leaf=leaf,
            drive=None,
            requested_by="an-operator",
            collaborators=collaborators,
        )
        await runner.drain(timeout_sec=5.0)

        assert first is True
        assert second is False
