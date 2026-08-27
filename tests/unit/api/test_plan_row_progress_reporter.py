"""The progress stamp lands on a durable row AND is announced.

A recursive decomposition persists its tree once, at the end, so a plan reads
``PLANNING`` with zero items for the whole run. Writing the snapshot is half
the answer: a row nobody is told about answers the question only for whoever
reloads, and the page an operator is already sitting on goes on showing the
snapshot it opened with for the hour the tree takes.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.lifecycle_helpers.decomposition_progress import (
    PlanRowProgressReporter,
)
from synthorg.core.decomposition_progress import DecompositionProgress
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task_enums import CoordinationTopology, TaskStructure
from synthorg.core.types import NotBlankStr
from tests._shared import as_uuid, make_app_state, sid
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit

_CREATED_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_OBJECTIVE = sid("task-root")

_PROGRESS = DecompositionProgress(
    sessions_spent=12,
    sessions_limit=40,
    deepest_level=2,
    units_planned=27,
    updated_at=datetime(2026, 1, 1, 12, 30, tzinfo=UTC),
)


def _shell(*, status: PlanStatus = PlanStatus.PLANNING) -> Plan:
    """Build the item-less plan a decomposition is filling.

    Returns:
        The shell.
    """
    return Plan(
        id=as_uuid("plan-shell"),
        project=NotBlankStr("beachhead"),
        project_name=NotBlankStr("Games"),
        objective_id=NotBlankStr("obj-001"),
        objective_title=NotBlankStr("Ship the game"),
        parent_task_id=NotBlankStr(_OBJECTIVE),
        items=(),
        task_structure=TaskStructure.SEQUENTIAL,
        coordination_topology=CoordinationTopology.AUTO,
        status=status,
        failure_reason=(
            NotBlankStr("planning died") if status is PlanStatus.FAILED else None
        ),
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


class _RecordingNotifier:
    """Remembers every plan it was asked to announce."""

    def __init__(self) -> None:
        self.announced: list[UUID] = []

    def __call__(self, plan: Plan) -> None:
        """Record one announcement."""
        self.announced.append(plan.id)


class TestItWritesTheSnapshot:
    async def test_the_shell_takes_the_stamp(self) -> None:
        backend = FakePersistenceBackend()
        await backend.plans.save(_shell())
        app_state = make_app_state(persistence=backend)

        await PlanRowProgressReporter(app_state).report(
            objective_task_id=_OBJECTIVE, progress=_PROGRESS
        )

        stored = await backend.plans.get(NotBlankStr(str(as_uuid("plan-shell"))))
        assert stored is not None
        assert stored.decomposition_progress == _PROGRESS

    async def test_a_plan_that_has_moved_on_takes_none(self) -> None:
        # The recovery sweep fails a shell whose decomposition died, under a
        # decomposition that may still be reporting. A late stamp landing on
        # it would revive the plan and discard the recorded reason.
        backend = FakePersistenceBackend()
        await backend.plans.save(_shell(status=PlanStatus.FAILED))
        app_state = make_app_state(persistence=backend)

        await PlanRowProgressReporter(app_state).report(
            objective_task_id=_OBJECTIVE, progress=_PROGRESS
        )

        stored = await backend.plans.get(NotBlankStr(str(as_uuid("plan-shell"))))
        assert stored is not None
        assert stored.decomposition_progress is None

    async def test_an_unconnected_backend_is_not_a_failure(self) -> None:
        # A deployment where nothing durable can carry the answer yet, which
        # is a shape rather than a fault, so it must not raise into the tree.
        await PlanRowProgressReporter(make_app_state()).report(
            objective_task_id=_OBJECTIVE, progress=_PROGRESS
        )


class TestItAnnouncesTheStamp:
    async def test_a_stamped_shell_is_announced(self) -> None:
        backend = FakePersistenceBackend()
        await backend.plans.save(_shell())
        notifier = _RecordingNotifier()
        app_state = make_app_state(persistence=backend)
        app_state.wire(ApiCoreStateSlice, plan_notifier=notifier)

        await PlanRowProgressReporter(app_state).report(
            objective_task_id=_OBJECTIVE, progress=_PROGRESS
        )

        assert notifier.announced == [as_uuid("plan-shell")]

    async def test_nothing_is_announced_when_nothing_took_the_stamp(self) -> None:
        # A subscriber refetches on this event, so announcing a row that did
        # not change is work with nothing behind it.
        backend = FakePersistenceBackend()
        await backend.plans.save(_shell(status=PlanStatus.FAILED))
        notifier = _RecordingNotifier()
        app_state = make_app_state(persistence=backend)
        app_state.wire(ApiCoreStateSlice, plan_notifier=notifier)

        await PlanRowProgressReporter(app_state).report(
            objective_task_id=_OBJECTIVE, progress=_PROGRESS
        )

        assert notifier.announced == []

    async def test_no_notifier_still_writes_the_row(self) -> None:
        # A deployment with no channels plugin still records progress; the
        # operator reloads for it rather than losing it.
        backend = FakePersistenceBackend()
        await backend.plans.save(_shell())
        app_state = make_app_state(persistence=backend)

        await PlanRowProgressReporter(app_state).report(
            objective_task_id=_OBJECTIVE, progress=_PROGRESS
        )

        stored = await backend.plans.get(NotBlankStr(str(as_uuid("plan-shell"))))
        assert stored is not None
        assert stored.decomposition_progress == _PROGRESS
