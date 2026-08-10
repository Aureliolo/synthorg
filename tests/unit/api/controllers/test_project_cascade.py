"""Unit tests for the project-delete cascade's contended plan retirement.

The rollup advances the same plan row whenever a task under it changes, so a
delete issued while the last task completes can lose the race. What matters is
not only that the retry happens but that it re-decides: an itemless shell can
only be FAILED, a filled plan can only be SUPERSEDED, and the race winner is
exactly what turns one into the other.

The removal pass that follows has its own question: it retires a plan's review
approval around the delete, so a plan the delete leaves standing is owed that
approval back.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.api.controllers._project_cascade import (
    _SUPERSEDE_ATTEMPTS,
    _delete_retired_plans,
    _supersede_plan,
)
from synthorg.api.lifecycle_helpers.plan_questions import PLAN_ID_METADATA_KEY
from synthorg.api.services.plan_service import PlanService
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import ConflictError, VersionConflictError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.types import NotBlankStr
from synthorg.persistence.plan_protocol import PlanDeleteOutcome, PlanRepository
from tests._shared import as_uuid, make_app_state, mock_of, sid
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)

#: Configured ``mock_of`` instance, typed loosely so the ``unittest.mock``
#: assertion API type-checks.
_Configured = Any  # type: ignore[explicit-any]


def _plan(*, status: PlanStatus, filled: bool, version: int = 1) -> Plan:
    items = (
        (
            PlanItem(
                id=NotBlankStr(sid("item-1")),
                title=NotBlankStr("Build it"),
                description=NotBlankStr("Implement the board."),
                acceptance_criteria=(NotBlankStr("it renders"),),
                expected_artifacts=(NotBlankStr("web/src/board.tsx"),),
            ),
        )
        if filled
        else ()
    )
    return Plan(
        id=as_uuid("plan-1"),
        project=NotBlankStr(sid("proj-1")),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship the board"),
        parent_task_id=NotBlankStr(sid("task-1")),
        items=items,
        status=status,
        version=version,
        created_at=_NOW,
        updated_at=_NOW,
    )


class TestSupersedeUnderContention:
    async def test_the_terminal_is_re_derived_from_the_race_winners_plan(
        self,
    ) -> None:
        """A shell filled by the race winner is superseded, not failed.

        The first attempt sees an itemless PLANNING shell, whose only legal
        terminal is FAILED. It loses to the decomposer, which fills the items
        and parks it for review. Re-deciding is what makes the retry legal:
        FAILED against a filled plan would be wrong, and SUPERSEDED against
        the shell violates the items CHECK.
        """
        shell = _plan(status=PlanStatus.PLANNING, filled=False)
        filled = _plan(status=PlanStatus.PENDING_REVIEW, filled=True, version=2)
        service: _Configured = mock_of[PlanService](
            sync_status=AsyncMock(side_effect=[VersionConflictError("lost"), filled])
        )
        repository: _Configured = mock_of[PlanRepository](
            get=AsyncMock(return_value=filled)
        )

        await _supersede_plan(service, repository, shell, requested_by="admin")

        first, second = service.sync_status.await_args_list
        assert first.args[1] is PlanStatus.FAILED
        assert first.kwargs["failure_reason"] == "project deleted"
        assert second.args[0] is filled
        assert second.args[1] is PlanStatus.SUPERSEDED
        # SUPERSEDED forbids a failure_reason, so re-deciding has to drop it.
        assert second.kwargs["failure_reason"] is None

    async def test_a_winner_that_already_retired_it_stops(self) -> None:
        # Nothing is left orphaned, so a second write would only lose another
        # race, and forcing a terminal over a terminal is not the cascade's
        # decision to make.
        live = _plan(status=PlanStatus.PENDING_REVIEW, filled=True)
        retired = _plan(status=PlanStatus.SUPERSEDED, filled=True, version=2)
        service: _Configured = mock_of[PlanService](
            sync_status=AsyncMock(side_effect=VersionConflictError("lost"))
        )
        repository: _Configured = mock_of[PlanRepository](
            get=AsyncMock(return_value=retired)
        )

        await _supersede_plan(service, repository, live, requested_by="admin")

        assert service.sync_status.await_count == 1

    async def test_a_plan_the_winner_deleted_stops(self) -> None:
        # The row is gone, so there is nothing to orphan and nothing to write.
        live = _plan(status=PlanStatus.PENDING_REVIEW, filled=True)
        service: _Configured = mock_of[PlanService](
            sync_status=AsyncMock(side_effect=VersionConflictError("lost"))
        )
        repository: _Configured = mock_of[PlanRepository](
            get=AsyncMock(return_value=None)
        )

        await _supersede_plan(service, repository, live, requested_by="admin")

        assert service.sync_status.await_count == 1

    async def test_an_exhausted_budget_aborts_the_delete(self) -> None:
        """Returning quietly would let the caller delete over a live plan.

        The caller removes the project once the cascade reports done, and
        ``plans.project`` carries no foreign key, so the plan would survive
        pointing at nothing. Contention is transient, so refusing this
        delete is what the operator can act on.
        """
        live = _plan(status=PlanStatus.PENDING_REVIEW, filled=True)
        service: _Configured = mock_of[PlanService](
            sync_status=AsyncMock(side_effect=VersionConflictError("lost"))
        )
        repository: _Configured = mock_of[PlanRepository](
            get=AsyncMock(return_value=live)
        )

        with pytest.raises(ConflictError):
            await _supersede_plan(service, repository, live, requested_by="admin")

        assert service.sync_status.await_count == _SUPERSEDE_ATTEMPTS


def _review_approval(plan: Plan) -> ApprovalItem:
    """Build the pending review approval the teardown has to decide about."""
    return ApprovalItem(
        id=as_uuid("review-held"),
        action_type=NotBlankStr("plan:approve"),
        title=NotBlankStr("Approve plan"),
        description=NotBlankStr("1 subtask(s)"),
        requested_by=NotBlankStr("user-1"),
        risk_level=ApprovalRiskLevel.MEDIUM,
        source=ApprovalSource.PLAN_REVIEW,
        status=ApprovalStatus.PENDING,
        created_at=_NOW,
        metadata={PLAN_ID_METADATA_KEY: str(plan.id)},
    )


class _RecordingApprovalStore:
    """Approval store double keeping whatever the retirement last wrote."""

    def __init__(self, items: tuple[ApprovalItem, ...]) -> None:
        self._items = {str(item.id): item for item in items}

    async def list_items(self, **_: object) -> tuple[ApprovalItem, ...]:
        return tuple(self._items.values())

    async def get(self, approval_id: object) -> ApprovalItem | None:
        return self._items.get(str(approval_id))

    async def save(self, item: ApprovalItem) -> ApprovalItem | None:
        self._items[str(item.id)] = item
        return item

    async def save_if_pending(self, item: ApprovalItem) -> ApprovalItem | None:
        self._items[str(item.id)] = item
        return item


async def _run_removal_pass(
    outcome: PlanDeleteOutcome,
) -> tuple[int, _RecordingApprovalStore]:
    """Page one project's plans with a delete answering *outcome*.

    Returns:
        How many plans the pass removed, and the approval store it wrote.
    """
    plan = _plan(status=PlanStatus.SUPERSEDED, filled=True)
    backend = FakePersistenceBackend()
    backend.mark_connected()
    await backend.plans.save(plan)
    store = _RecordingApprovalStore((_review_approval(plan),))
    service: _Configured = mock_of[PlanService](
        delete_for_project_teardown=AsyncMock(return_value=outcome)
    )

    deleted = await _delete_retired_plans(
        make_app_state(
            persistence=backend,
            slices={ApprovalStateSlice: {"store": store}},
        ),
        service,
        NotBlankStr(sid("proj-1")),
        requested_by="user-1",
    )
    return deleted, store


class TestRemovalPassApprovalRetirement:
    """A plan the delete leaves standing keeps a decidable review.

    Retirement is scoped to the delete, and until now the only thing that
    undid it was an exception. Live work refuses this delete by returning,
    which left the approval expired against a plan still listed, still
    reviewable, and no longer answerable.
    """

    async def test_a_refused_plan_keeps_its_review_answerable(self) -> None:
        deleted, store = await _run_removal_pass(
            PlanDeleteOutcome(deleted=False, live_task_count=1)
        )

        assert deleted == 0
        assert [item.status for item in await store.list_items()] == [
            ApprovalStatus.PENDING
        ]

    async def test_a_plan_already_gone_keeps_its_review_retired(self) -> None:
        """Gone is gone, whoever removed it.

        Restoring here would put a pending question back against a plan that
        no longer resolves, which is the dangling approval the retirement
        exists to prevent.
        """
        deleted, store = await _run_removal_pass(
            PlanDeleteOutcome(deleted=False, live_task_count=0)
        )

        assert deleted == 0
        assert [item.status for item in await store.list_items()] == [
            ApprovalStatus.EXPIRED
        ]

    async def test_a_removed_plan_is_counted_and_stays_retired(self) -> None:
        deleted, store = await _run_removal_pass(PlanDeleteOutcome(deleted=True))

        assert deleted == 1
        assert [item.status for item in await store.list_items()] == [
            ApprovalStatus.EXPIRED
        ]
