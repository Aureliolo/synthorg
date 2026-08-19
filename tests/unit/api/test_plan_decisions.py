"""What the plans page is told about a decision waiting on the operator.

A plan's status records what the organisation last did with it. It cannot say
that the initiative has stopped and needs a person, so an operator scanning the
board reads ``executing`` on a plan whose every item is dead. The open approval
holds that fact, and these pin that it reaches the row.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.api._plan_decisions import _MAX_REASON_CHARS, pending_plan_decisions
from synthorg.api.approval_store import ApprovalStore
from synthorg.api.dto_named_rows import PlanPendingDecision, PlanRow
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.approval.initiative_stall import (
    ESCALATION_ACTOR,
    INITIATIVE_STALL_ACTION_TYPE,
    PLAN_ID_METADATA_KEY,
)
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.approval import ApprovalItem
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.types import NotBlankStr
from tests._shared import as_uuid, make_app_state, mock_of, sid
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)
_PLAN_ID = "plan-1"
_OTHER_PLAN = "plan-2"
_PARENT = sid("parent-1")
_ITEM_A = sid("item-a")


def _plan(plan_id: str = _PLAN_ID) -> Plan:
    """Build a plan the row is derived from.

    Returns:
        The plan, carrying one work item.
    """
    return Plan(
        id=as_uuid(plan_id),
        project=NotBlankStr(sid("proj-1")),
        project_name=NotBlankStr("Platform"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship the thing"),
        parent_task_id=NotBlankStr(_PARENT),
        created_at=_NOW,
        updated_at=_NOW,
        items=(
            PlanItem(
                id=NotBlankStr(_ITEM_A),
                title=NotBlankStr("Build it"),
                description=NotBlankStr("Build the thing"),
                acceptance_criteria=(NotBlankStr("it is done"),),
                expected_artifacts=(NotBlankStr("src/thing.py"),),
            ),
        ),
        status=PlanStatus.EXECUTING,
    )


def _decision(
    *,
    plan_id: str = _PLAN_ID,
    description: str = "This initiative can no longer advance: all failed.",
    action_type: str = INITIATIVE_STALL_ACTION_TYPE,
    status: ApprovalStatus = ApprovalStatus.PENDING,
) -> ApprovalItem:
    """Build a decision open against *plan_id*.

    Returns:
        The approval item.
    """
    return ApprovalItem(
        id=as_uuid(f"approval-{plan_id}"),
        action_type=NotBlankStr(action_type),
        title=NotBlankStr("Initiative stopped: Ship the thing"),
        description=NotBlankStr(description),
        requested_by=NotBlankStr(ESCALATION_ACTOR),
        risk_level=ApprovalRiskLevel.HIGH,
        status=status,
        created_at=_NOW,
        decided_at=None if status is ApprovalStatus.PENDING else _NOW,
        decided_by=None if status is ApprovalStatus.PENDING else NotBlankStr("op"),
        decision_reason=(
            None if status is not ApprovalStatus.REJECTED else NotBlankStr("ended")
        ),
        task_id=NotBlankStr(_PARENT),
        metadata={PLAN_ID_METADATA_KEY: sid(plan_id)},
    )


async def _state(store: ApprovalStoreProtocol | None) -> AppState:
    """Build an app state whose approvals slice holds *store*.

    Returns:
        The app state.
    """
    return make_app_state(
        persistence=FakePersistenceBackend(),
        approval_store=store,
    )


class TestResolvingThePage:
    async def test_a_plan_with_an_open_decision_gets_one(self) -> None:
        store = ApprovalStore()
        await store.add(_decision())

        waiting = await pending_plan_decisions(await _state(store), [sid(_PLAN_ID)])

        assert set(waiting) == {sid(_PLAN_ID)}
        assert waiting[sid(_PLAN_ID)].requested_by == ESCALATION_ACTOR

    async def test_a_plan_with_none_is_absent_rather_than_null(self) -> None:
        store = ApprovalStore()

        waiting = await pending_plan_decisions(await _state(store), [sid(_PLAN_ID)])

        assert waiting == {}

    async def test_a_decision_about_another_plan_is_not_borrowed(self) -> None:
        """The read is page-wide, so the filter has to be per plan."""
        store = ApprovalStore()
        await store.add(_decision(plan_id=_OTHER_PLAN))

        waiting = await pending_plan_decisions(await _state(store), [sid(_PLAN_ID)])

        assert waiting == {}

    async def test_a_decided_decision_is_no_longer_waiting(self) -> None:
        store = ApprovalStore()
        await store.add(_decision(status=ApprovalStatus.REJECTED))

        waiting = await pending_plan_decisions(await _state(store), [sid(_PLAN_ID)])

        assert waiting == {}

    async def test_an_empty_page_reads_nothing_at_all(self) -> None:
        """One store read per response, and none at all for an empty page."""
        store = mock_of[ApprovalStoreProtocol](list_items=AsyncMock(return_value=[]))

        waiting = await pending_plan_decisions(await _state(store), [])

        assert waiting == {}
        store.list_items.assert_not_awaited()

    async def test_a_whole_page_costs_one_read(self) -> None:
        store = mock_of[ApprovalStoreProtocol](
            list_items=AsyncMock(return_value=[_decision()])
        )

        await pending_plan_decisions(
            await _state(store), [sid(_PLAN_ID), sid(_OTHER_PLAN)]
        )

        assert store.list_items.await_count == 1

    async def test_no_store_at_all_answers_nothing(self) -> None:
        waiting = await pending_plan_decisions(await _state(None), [sid(_PLAN_ID)])

        assert waiting == {}

    async def test_a_degraded_store_costs_the_badge_not_the_board(self) -> None:
        """The plans are complete without it; a 500 here would lose the page."""
        store = mock_of[ApprovalStoreProtocol](
            list_items=AsyncMock(side_effect=RuntimeError("approvals unavailable"))
        )

        waiting = await pending_plan_decisions(await _state(store), [sid(_PLAN_ID)])

        assert waiting == {}


class TestTheReasonARowShows:
    async def test_it_is_the_first_line_of_the_briefing(self) -> None:
        """The whole description belongs on the approval, not in a row."""
        store = ApprovalStore()
        await store.add(_decision(description="First line.\nSecond line."))

        waiting = await pending_plan_decisions(await _state(store), [sid(_PLAN_ID)])

        assert waiting[sid(_PLAN_ID)].reason == "First line."

    async def test_a_long_line_is_bounded_including_its_ellipsis(self) -> None:
        """A cap the marker pushes past is not a cap on what is rendered."""
        store = ApprovalStore()
        await store.add(_decision(description="w" * (_MAX_REASON_CHARS * 2)))

        waiting = await pending_plan_decisions(await _state(store), [sid(_PLAN_ID)])

        reason = waiting[sid(_PLAN_ID)].reason
        assert len(reason) <= _MAX_REASON_CHARS
        assert reason.endswith("...")


class TestTheRow:
    def test_the_row_keeps_only_its_own_decision(self) -> None:
        """The map covers a page; the field means one plan on every surface."""
        store_map = {
            sid(_PLAN_ID): _row_decision(),
        }

        row = PlanRow.of(_plan(_OTHER_PLAN), {}, store_map)

        assert row.pending_decision is None

    def test_a_page_without_the_map_is_not_a_page_without_decisions(self) -> None:
        """``None`` means unresolved, which renders the same as none waiting."""
        row = PlanRow.of(_plan(), {})

        assert row.pending_decision is None

    def test_the_row_carries_the_decision_raised_against_it(self) -> None:
        row = PlanRow.of(_plan(), {}, {sid(_PLAN_ID): _row_decision()})

        assert row.pending_decision is not None
        assert row.pending_decision.requested_by == ESCALATION_ACTOR


def _row_decision() -> PlanPendingDecision:
    """Build the decision a row carries.

    Returns:
        The resolved decision, through the resolver's own model.
    """
    return PlanPendingDecision(
        approval_id=NotBlankStr("approval-1"),
        action_type=NotBlankStr(INITIATIVE_STALL_ACTION_TYPE),
        title=NotBlankStr("Initiative stopped: Ship the thing"),
        reason=NotBlankStr("all failed"),
        requested_by=NotBlankStr(ESCALATION_ACTOR),
    )
