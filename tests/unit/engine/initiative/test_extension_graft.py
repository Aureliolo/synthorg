"""Tests for grafting a workstream's next extension onto a live plan."""

from dataclasses import replace
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
from synthorg.core.plan_tree_validation import describe_malformed_tree
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
from synthorg.engine.initiative.extension_autonomy import EffectiveAutonomyForPlan
from synthorg.engine.initiative.extension_graft import (
    DEFAULT_EXTENSION_ENABLED,
    DEFAULT_EXTENSION_MAX_GENERATIONS,
    ExtensionCollaborators,
    consider_extension,
    graft_extension,
    grant_extension,
    resolve_extension_enabled,
    resolve_extension_max_generations,
    resolve_extension_timeout_seconds,
)
from synthorg.engine.initiative.extension_state import ExtensionDisposition
from synthorg.engine.initiative.ports import DriveOutcome
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


def _nested_decomposition(root_id: str, child_id: str) -> DecompositionResult:
    """A root subtask split one level further, so ``child_id`` already has a parent."""
    root = _decomposition(root_id)
    child = SubtaskDefinition(
        id=sid(child_id),
        title=f"Split {child_id}",
        description="A narrower piece of the root subtask's own claim",
        acceptance_criteria=(NotBlankStr("it works"),),
        expected_artifacts=(NotBlankStr("src/split.py"),),
    )
    child_level = DecompositionResult(
        plan=DecompositionPlan(
            parent_task_id=str(sid(root_id)),
            subtasks=(child,),
            task_structure=TaskStructure.SEQUENTIAL,
        ),
        depth=1,
        created_tasks=(
            Task(
                id=as_uuid(child_id),
                title=f"Split {child_id}",
                description="A narrower piece of the root subtask's own claim",
                type=TaskType.DEVELOPMENT,
                priority=Priority.MEDIUM,
                project=sid(_PROJECT),
                created_by="manager",
                parent_task_id=str(sid(root_id)),
            ),
        ),
    )
    return root.model_copy(update={"children": (child_level,)})


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


def _collaborators(
    backend: FakePersistenceBackend,
    *,
    decompose: AsyncMock,
    resolver: ConfigResolver | None = None,
    runner: StageRunner | None = None,
    effective_autonomy: EffectiveAutonomyForPlan | None = None,
) -> ExtensionCollaborators:
    return ExtensionCollaborators(
        persistence=backend,
        task_engine=mock_of[TaskEngine](get_task=AsyncMock(return_value=_leaf_task())),
        decomposition_service=mock_of[DecompositionService](decompose_task=decompose),
        config_resolver=resolver
        or mock_of[ConfigResolver](
            get_bool=AsyncMock(return_value=True),
            get_int=AsyncMock(return_value=2),
            get_float=AsyncMock(return_value=600.0),
        ),
        runner=runner
        or StageRunner(
            owner="test.extension",
            clock=FakeClock(),
            skipped_event="test.extension.skipped",
            failed_event="test.extension.failed",
        ),
        owner_resolver=_no_owner,
        roster_resolver=_no_roster,
        effective_autonomy=effective_autonomy or _auto_approve_autonomy,
        clock=FakeClock(),
    )


class TestResolveExtensionEnabled:
    """The master switch, live-read with a safe fallback."""

    async def test_no_resolver_returns_the_default(self) -> None:
        assert await resolve_extension_enabled(None) is DEFAULT_EXTENSION_ENABLED

    async def test_reads_the_live_setting(self) -> None:
        resolver: ConfigResolver = mock_of[ConfigResolver](
            get_bool=AsyncMock(return_value=False)
        )
        assert await resolve_extension_enabled(resolver) is False

    async def test_a_failed_read_falls_back_to_the_default(self) -> None:
        resolver: ConfigResolver = mock_of[ConfigResolver](
            get_bool=AsyncMock(side_effect=RuntimeError("settings down"))
        )
        assert await resolve_extension_enabled(resolver) is DEFAULT_EXTENSION_ENABLED


class TestResolveExtensionMaxGenerations:
    """The per-workstream generation cap, live-read with a safe fallback."""

    async def test_no_resolver_returns_the_default(self) -> None:
        result = await resolve_extension_max_generations(None)
        assert result == DEFAULT_EXTENSION_MAX_GENERATIONS

    async def test_reads_the_live_setting(self) -> None:
        resolver: ConfigResolver = mock_of[ConfigResolver](
            get_int=AsyncMock(return_value=5)
        )
        assert await resolve_extension_max_generations(resolver) == 5

    async def test_a_failed_read_falls_back_to_the_default(self) -> None:
        resolver: ConfigResolver = mock_of[ConfigResolver](
            get_int=AsyncMock(side_effect=RuntimeError("settings down"))
        )
        result = await resolve_extension_max_generations(resolver)
        assert result == DEFAULT_EXTENSION_MAX_GENERATIONS


class TestResolveExtensionTimeoutSeconds:
    """The per-attempt wall-clock ceiling, live-read with a safe fallback."""

    async def test_no_resolver_returns_the_default(self) -> None:
        assert await resolve_extension_timeout_seconds(None) == 600.0

    async def test_reads_the_live_setting(self) -> None:
        resolver: ConfigResolver = mock_of[ConfigResolver](
            get_float=AsyncMock(return_value=120.0)
        )
        assert await resolve_extension_timeout_seconds(resolver) == 120.0

    async def test_a_non_positive_read_falls_back_to_the_default(self) -> None:
        resolver: ConfigResolver = mock_of[ConfigResolver](
            get_float=AsyncMock(return_value=0.0)
        )
        assert await resolve_extension_timeout_seconds(resolver) == 600.0

    async def test_a_failed_read_falls_back_to_the_default(self) -> None:
        resolver: ConfigResolver = mock_of[ConfigResolver](
            get_float=AsyncMock(side_effect=RuntimeError("settings down"))
        )
        assert await resolve_extension_timeout_seconds(resolver) == 600.0


class TestGraftExtension:
    """Decompose the leaf's remaining scope and graft it under the leaf."""

    async def test_the_extension_is_declared_against_the_leafs_own_claim(self) -> None:
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        tree = PlanTree.of(plan.items)
        decompose = AsyncMock(return_value=_decomposition("sub-1"))
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)

        await graft_extension(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            tree=tree,
            collaborators=_collaborators(backend, decompose=decompose),
            drive=None,
        )

        assert decompose.await_args is not None
        context = decompose.await_args.args[1]
        assert context.objective_criteria == leaf.satisfies
        assert context.current_depth == 0

    async def test_new_items_are_reparented_under_the_leaf(self) -> None:
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        tree = PlanTree.of(plan.items)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        decompose = AsyncMock(return_value=_decomposition("sub-1", "sub-2"))

        updated = await graft_extension(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            tree=tree,
            collaborators=_collaborators(backend, decompose=decompose),
            drive=None,
        )

        assert updated is not None
        new_items = [item for item in updated.items if item.parent_id == leaf.id]
        new_ids = {item.id for item in new_items}
        # The decomposition's own two items, plus one more: the assembly of
        # them, since leaf's own already-completed task cannot be rewritten
        # into one (see extension_graft._extension_assembly_item).
        assert {sid("sub-1"), sid("sub-2")} <= new_ids
        assert len(new_items) == 3
        assert updated.version == plan.version + 1

    async def test_a_deeper_level_keeps_its_own_parent(self) -> None:
        """Only the root level is parentless; a genuine child keeps its parent."""
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        tree = PlanTree.of(plan.items)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        decompose = AsyncMock(
            return_value=_nested_decomposition("sub-1", "sub-1-child")
        )

        updated = await graft_extension(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            tree=tree,
            collaborators=_collaborators(backend, decompose=decompose),
            drive=None,
        )

        assert updated is not None
        by_id = {item.id: item for item in updated.items}
        assert by_id[sid("sub-1")].parent_id == leaf.id
        assert by_id[sid("sub-1-child")].parent_id == sid("sub-1")

    async def test_the_assembly_item_depends_on_every_new_sibling(self) -> None:
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        tree = PlanTree.of(plan.items)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        decompose = AsyncMock(return_value=_decomposition("sub-1", "sub-2"))

        updated = await graft_extension(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            tree=tree,
            collaborators=_collaborators(backend, decompose=decompose),
            drive=None,
        )

        assert updated is not None
        new_items = [item for item in updated.items if item.parent_id == leaf.id]
        assembly = next(
            item for item in new_items if item.id not in {sid("sub-1"), sid("sub-2")}
        )
        assert set(assembly.dependencies) == {sid("sub-1"), sid("sub-2")}
        assert assembly.unsplit_reason is None

    async def test_the_grafted_tree_is_well_formed(self) -> None:
        """A malformed graft would ship silently: model_copy skips validators."""
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        tree = PlanTree.of(plan.items)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        decompose = AsyncMock(return_value=_decomposition("sub-1", "sub-2"))

        updated = await graft_extension(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            tree=tree,
            collaborators=_collaborators(backend, decompose=decompose),
            drive=None,
        )

        assert updated is not None
        assert describe_malformed_tree(updated.items) == ()

    async def test_the_original_items_survive_byte_for_byte(self) -> None:
        workstream = _item(_WORKSTREAM)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, leaf)
        tree = PlanTree.of(plan.items)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        decompose = AsyncMock(return_value=_decomposition("sub-1"))

        updated = await graft_extension(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            tree=tree,
            collaborators=_collaborators(backend, decompose=decompose),
            drive=None,
        )

        assert updated is not None
        assert workstream in updated.items
        assert leaf in updated.items

    async def test_only_the_new_tasks_are_filed(self) -> None:
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        tree = PlanTree.of(plan.items)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        await backend.tasks.save(_leaf_task())
        decompose = AsyncMock(return_value=_decomposition("sub-1"))

        updated = await graft_extension(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            tree=tree,
            collaborators=_collaborators(backend, decompose=decompose),
            drive=None,
        )

        assert updated is not None
        filed = await backend.tasks.get(str(sid("sub-1")))
        assert filed is not None
        # The pre-existing leaf task is untouched, never re-saved.
        leaf_row = await backend.tasks.get(str(_LEAF))
        assert leaf_row is not None
        # The assembly item's own task was filed too, under its own fresh id.
        assembly_item = next(
            item
            for item in updated.items
            if item.parent_id == leaf.id and item.id != sid("sub-1")
        )
        assembly_task = await backend.tasks.get(str(assembly_item.id))
        assert assembly_task is not None

    async def test_the_driver_is_called_once_on_a_successful_graft(self) -> None:
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        tree = PlanTree.of(plan.items)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        drive = AsyncMock(return_value=DriveOutcome.DRIVING)
        decompose = AsyncMock(return_value=_decomposition("sub-1"))

        updated = await graft_extension(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            tree=tree,
            collaborators=_collaborators(backend, decompose=decompose),
            drive=drive,
        )

        assert updated is not None
        assert drive.await_count == 1
        assert drive.await_args is not None
        (driven_plan,), _ = drive.await_args
        assert driven_plan is updated
        assert any(item.parent_id == leaf.id for item in updated.items)

    async def test_a_refused_drive_returns_none(self) -> None:
        """A refused drive means the plan cannot be driven at all right now.

        The graft's own items and tasks are already persisted (and stay so:
        the next pass re-derives the same graft and retries), but the caller
        must be told this attempt did not land, on the same reasoning
        ``_dispatch_units`` routes a refused drive to a replan rather than
        reading it as success.
        """
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        tree = PlanTree.of(plan.items)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        drive = AsyncMock(return_value=DriveOutcome.REFUSED)
        decompose = AsyncMock(return_value=_decomposition("sub-1"))

        updated = await graft_extension(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            tree=tree,
            collaborators=_collaborators(backend, decompose=decompose),
            drive=drive,
        )

        assert updated is None
        # The items and tasks it already wrote are not rolled back.
        fresh = await backend.plans.get(str(plan.id))
        assert fresh is not None
        assert any(item.parent_id == leaf.id for item in fresh.items)

    async def test_a_held_drive_still_returns_the_grafted_plan(self) -> None:
        """Another driver already owns the plan; that is not this graft's failure."""
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        tree = PlanTree.of(plan.items)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        drive = AsyncMock(return_value=DriveOutcome.HELD)
        decompose = AsyncMock(return_value=_decomposition("sub-1"))

        updated = await graft_extension(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            tree=tree,
            collaborators=_collaborators(backend, decompose=decompose),
            drive=drive,
        )

        assert updated is not None
        assert any(item.parent_id == leaf.id for item in updated.items)

    async def test_a_version_conflict_is_retried_once_and_succeeds(self) -> None:
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        tree = PlanTree.of(plan.items)
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
        decompose = AsyncMock(return_value=_decomposition("sub-1"))

        updated = await graft_extension(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            tree=tree,
            collaborators=_collaborators(backend, decompose=decompose),
            drive=None,
        )

        assert updated is not None
        assert calls["count"] == 2

    async def test_two_version_conflicts_give_up(self) -> None:
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        tree = PlanTree.of(plan.items)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)

        async def always_conflicts(
            candidate: Plan, *, expected_version: int | None = None
        ) -> None:
            del candidate, expected_version
            msg = "moved"
            raise PersistenceVersionConflictError(msg)

        backend.plans.update = always_conflicts  # type: ignore[method-assign,assignment]
        decompose = AsyncMock(return_value=_decomposition("sub-1"))

        updated = await graft_extension(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            tree=tree,
            collaborators=_collaborators(backend, decompose=decompose),
            drive=None,
        )

        assert updated is None

    async def test_the_plan_deleted_mid_conflict_gives_up_without_raising(self) -> None:
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(_item(_WORKSTREAM), leaf)
        tree = PlanTree.of(plan.items)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)

        async def conflict_then_vanish(
            candidate: Plan, *, expected_version: int | None = None
        ) -> None:
            del candidate, expected_version
            await backend.plans.delete(str(plan.id))
            msg = "moved"
            raise PersistenceVersionConflictError(msg)

        backend.plans.update = conflict_then_vanish  # type: ignore[method-assign,assignment]
        decompose = AsyncMock(return_value=_decomposition("sub-1"))

        updated = await graft_extension(
            plan,
            leaf,
            leaf_task=_leaf_task(),
            roster=DecompositionContext(),
            tree=tree,
            collaborators=_collaborators(backend, decompose=decompose),
            drive=None,
        )

        assert updated is None


class TestConsiderExtension:
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

        disposition = await consider_extension(
            plan=plan,
            tree=tree,
            workstream=workstream,
            leaf=leaf,
            drive=None,
            collaborators=_collaborators(
                FakePersistenceBackend(), decompose=decompose, resolver=resolver
            ),
        )

        assert disposition is ExtensionDisposition.DISABLED
        decompose.assert_not_awaited()

    async def test_generation_at_the_cap_is_refused(self) -> None:
        workstream = _item(_WORKSTREAM)
        extended = _item(
            sid("extended"), parent_id=_WORKSTREAM, unsplit_reason="oversized"
        )
        child = _item(sid("child"), parent_id=extended.id)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, extended, child, leaf)
        tree = PlanTree.of(plan.items)
        decompose = AsyncMock()
        resolver: ConfigResolver = mock_of[ConfigResolver](
            get_bool=AsyncMock(return_value=True),
            get_int=AsyncMock(return_value=1),
        )

        disposition = await consider_extension(
            plan=plan,
            tree=tree,
            workstream=workstream,
            leaf=leaf,
            drive=None,
            collaborators=_collaborators(
                FakePersistenceBackend(), decompose=decompose, resolver=resolver
            ),
        )

        assert disposition is ExtensionDisposition.BUDGET_EXHAUSTED
        decompose.assert_not_awaited()

    async def test_an_extension_already_in_flight_collapses_into_it(self) -> None:
        workstream = _item(_WORKSTREAM)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, leaf)
        tree = PlanTree.of(plan.items)
        runner = StageRunner(
            owner="test.extension",
            clock=FakeClock(),
            skipped_event="test.extension.skipped",
            failed_event="test.extension.failed",
        )
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        decompose = AsyncMock(return_value=_decomposition("sub-1"))

        first = await consider_extension(
            plan=plan,
            tree=tree,
            workstream=workstream,
            leaf=leaf,
            drive=None,
            collaborators=_collaborators(backend, decompose=decompose, runner=runner),
        )
        second = await consider_extension(
            plan=plan,
            tree=tree,
            workstream=workstream,
            leaf=leaf,
            drive=None,
            collaborators=_collaborators(backend, decompose=decompose, runner=runner),
        )
        await runner.drain(timeout_sec=5.0)

        assert first is ExtensionDisposition.GRAFTED
        assert second is ExtensionDisposition.ALREADY_RUNNING

    async def test_two_leaves_in_one_workstream_serialise_not_double_spend(
        self,
    ) -> None:
        """Keyed per workstream: two oversized leaves must not both pass the
        generation cap read against the same pre-graft count."""
        workstream = _item(_WORKSTREAM)
        other_leaf = _item(
            sid("other-leaf"), parent_id=_WORKSTREAM, unsplit_reason="oversized"
        )
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, other_leaf, leaf)
        tree = PlanTree.of(plan.items)
        runner = StageRunner(
            owner="test.extension",
            clock=FakeClock(),
            skipped_event="test.extension.skipped",
            failed_event="test.extension.failed",
        )
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        decompose = AsyncMock(return_value=_decomposition("sub-1"))

        first = await consider_extension(
            plan=plan,
            tree=tree,
            workstream=workstream,
            leaf=other_leaf,
            drive=None,
            collaborators=_collaborators(backend, decompose=decompose, runner=runner),
        )
        second = await consider_extension(
            plan=plan,
            tree=tree,
            workstream=workstream,
            leaf=leaf,
            drive=None,
            collaborators=_collaborators(backend, decompose=decompose, runner=runner),
        )
        await runner.drain(timeout_sec=5.0)

        assert first is ExtensionDisposition.GRAFTED
        assert second is ExtensionDisposition.ALREADY_RUNNING

    async def test_the_happy_path_grafts_the_extension(self) -> None:
        workstream = _item(_WORKSTREAM)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, leaf)
        tree = PlanTree.of(plan.items)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        decompose = AsyncMock(return_value=_decomposition("sub-1"))
        runner = StageRunner(
            owner="test.extension",
            clock=FakeClock(),
            skipped_event="test.extension.skipped",
            failed_event="test.extension.failed",
        )

        disposition = await consider_extension(
            plan=plan,
            tree=tree,
            workstream=workstream,
            leaf=leaf,
            drive=None,
            collaborators=_collaborators(backend, decompose=decompose, runner=runner),
        )
        await runner.drain(timeout_sec=5.0)

        assert disposition is ExtensionDisposition.GRAFTED
        fresh = await backend.plans.get(str(plan.id))
        assert fresh is not None
        assert any(item.parent_id == leaf.id for item in fresh.items)

    async def test_a_leaf_already_extended_by_another_writer_is_a_no_op(self) -> None:
        """The re-check inside the detached work catches a stale ask."""
        workstream = _item(_WORKSTREAM)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, leaf)
        tree = PlanTree.of(plan.items)
        backend = FakePersistenceBackend()
        # Another writer already extended the leaf by the time the detached
        # work actually runs: it now has children, even though its own
        # unsplit_reason (never cleared once written) is still set.
        child = _item(sid("already-there"), parent_id=leaf.id)
        already_extended_plan = plan.model_copy(
            update={"items": (workstream, leaf, child), "version": 2}
        )
        await backend.plans.save(already_extended_plan)
        decompose = AsyncMock(return_value=_decomposition("sub-1"))
        runner = StageRunner(
            owner="test.extension",
            clock=FakeClock(),
            skipped_event="test.extension.skipped",
            failed_event="test.extension.failed",
        )

        await consider_extension(
            plan=plan,
            tree=tree,
            workstream=workstream,
            leaf=leaf,
            drive=None,
            collaborators=_collaborators(backend, decompose=decompose, runner=runner),
        )
        await runner.drain(timeout_sec=5.0)

        decompose.assert_not_awaited()

    async def test_the_plan_deleted_before_the_detached_work_runs_is_a_no_op(
        self,
    ) -> None:
        """The re-read inside the detached work catches an operator deletion."""
        workstream = _item(_WORKSTREAM)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, leaf)
        tree = PlanTree.of(plan.items)
        # Never saved: the detached work's own re-read finds nothing.
        backend = FakePersistenceBackend()
        decompose = AsyncMock(return_value=_decomposition("sub-1"))
        runner = StageRunner(
            owner="test.extension",
            clock=FakeClock(),
            skipped_event="test.extension.skipped",
            failed_event="test.extension.failed",
        )

        await consider_extension(
            plan=plan,
            tree=tree,
            workstream=workstream,
            leaf=leaf,
            drive=None,
            collaborators=_collaborators(backend, decompose=decompose, runner=runner),
        )
        await runner.drain(timeout_sec=5.0)

        decompose.assert_not_awaited()

    async def test_a_missing_leaf_task_is_a_no_op(self) -> None:
        """The re-check finds the leaf still needs extending but has no task."""
        workstream = _item(_WORKSTREAM)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, leaf)
        tree = PlanTree.of(plan.items)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        decompose = AsyncMock(return_value=_decomposition("sub-1"))
        runner = StageRunner(
            owner="test.extension",
            clock=FakeClock(),
            skipped_event="test.extension.skipped",
            failed_event="test.extension.failed",
        )
        collaborators = _collaborators(backend, decompose=decompose, runner=runner)
        no_task_collaborators = replace(
            collaborators,
            task_engine=mock_of[TaskEngine](get_task=AsyncMock(return_value=None)),
        )

        await consider_extension(
            plan=plan,
            tree=tree,
            workstream=workstream,
            leaf=leaf,
            drive=None,
            collaborators=no_task_collaborators,
        )
        await runner.drain(timeout_sec=5.0)

        decompose.assert_not_awaited()


class TestConsiderExtensionAutonomyGate:
    """The deterministic gate is decided before any detached work starts."""

    async def test_human_approval_required_asks_before_any_decomposition(
        self,
    ) -> None:
        workstream = _item(_WORKSTREAM)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, leaf)
        tree = PlanTree.of(plan.items)
        decompose = AsyncMock()

        disposition = await consider_extension(
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

        assert disposition is ExtensionDisposition.ASKED
        decompose.assert_not_awaited()

    async def test_unresolvable_autonomy_fails_closed_to_asked(self) -> None:
        workstream = _item(_WORKSTREAM)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, leaf)
        tree = PlanTree.of(plan.items)
        decompose = AsyncMock()

        disposition = await consider_extension(
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

        assert disposition is ExtensionDisposition.ASKED
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
            owner="test.extension",
            clock=FakeClock(),
            skipped_event="test.extension.skipped",
            failed_event="test.extension.failed",
        )

        disposition = await consider_extension(
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

        assert disposition is ExtensionDisposition.GRAFTED


class TestGrantExtension:
    """A person's grant bypasses every automatic guard but ALREADY_RUNNING."""

    async def test_grants_past_a_human_approval_gate(self) -> None:
        workstream = _item(_WORKSTREAM)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, leaf)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        decompose = AsyncMock(return_value=_decomposition("sub-1"))
        runner = StageRunner(
            owner="test.extension",
            clock=FakeClock(),
            skipped_event="test.extension.skipped",
            failed_event="test.extension.failed",
        )

        started = await grant_extension(
            plan=plan,
            workstream=workstream,
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
        workstream = _item(_WORKSTREAM)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, leaf)
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)
        decompose = AsyncMock(return_value=_decomposition("sub-1"))
        runner = StageRunner(
            owner="test.extension",
            clock=FakeClock(),
            skipped_event="test.extension.skipped",
            failed_event="test.extension.failed",
        )
        collaborators = _collaborators(backend, decompose=decompose, runner=runner)

        first = await grant_extension(
            plan=plan,
            workstream=workstream,
            leaf=leaf,
            drive=None,
            requested_by="an-operator",
            collaborators=collaborators,
        )
        second = await grant_extension(
            plan=plan,
            workstream=workstream,
            leaf=leaf,
            drive=None,
            requested_by="an-operator",
            collaborators=collaborators,
        )
        await runner.drain(timeout_sec=5.0)

        assert first is True
        assert second is False
