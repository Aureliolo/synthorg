"""Tests for retiring a plan's parked review approval when the plan is deleted.

A pending ``PLAN_REVIEW`` approval outlives the plan row it names. Approving
it afterwards drives the resume path at a plan that no longer exists, which
fails the parent task over a decision about something already deleted, so
the delete has to take the approval with it.
"""

from datetime import UTC, datetime

import pytest

from synthorg.api.controllers._plan_approval_retire import retire_review_approval
from synthorg.api.lifecycle_helpers.plan_questions import PLAN_ID_METADATA_KEY
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import ConflictError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.types import NotBlankStr
from tests._shared import as_uuid, mock_of, sid
from tests._shared.app_state import make_app_state

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
_PLAN_ID = "doomed"


def _plan() -> Plan:
    return Plan(
        id=as_uuid(_PLAN_ID),
        project=NotBlankStr("beachhead"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship the board"),
        parent_task_id=NotBlankStr(sid("task-root")),
        created_at=_NOW,
        updated_at=_NOW,
        items=(
            PlanItem(
                id=NotBlankStr(sid("item-1")),
                title=NotBlankStr("Build"),
                description=NotBlankStr("Do the work"),
                acceptance_criteria=(NotBlankStr("it is done"),),
                expected_artifacts=(NotBlankStr("src/work.py"),),
            ),
        ),
    )


def _approval(
    approval_id: str,
    *,
    source: ApprovalSource = ApprovalSource.PLAN_REVIEW,
    plan_id: str | None = str(as_uuid(_PLAN_ID)),
) -> ApprovalItem:
    metadata = {} if plan_id is None else {PLAN_ID_METADATA_KEY: plan_id}
    return ApprovalItem(
        id=as_uuid(approval_id),
        action_type=NotBlankStr("plan:approve"),
        title=NotBlankStr("Approve plan"),
        description=NotBlankStr("1 subtask(s)"),
        requested_by=NotBlankStr("user-1"),
        risk_level=ApprovalRiskLevel.MEDIUM,
        source=source,
        status=ApprovalStatus.PENDING,
        created_at=_NOW,
        metadata=metadata,
    )


class _RecordingStore:
    """Approval store double recording the conditional writes it received."""

    def __init__(self, items: tuple[ApprovalItem, ...]) -> None:
        self._items = items
        self.saved: list[ApprovalItem] = []
        # ``False`` stands for a decision landing between the read and the
        # conditional write, which is what the real store reports by
        # answering ``None``.
        self.cas_wins = True

    async def list_items(self, **_: object) -> tuple[ApprovalItem, ...]:
        return self._items

    async def save_if_pending(self, item: ApprovalItem) -> ApprovalItem | None:
        self.saved.append(item)
        return item if self.cas_wins else None


def _state(store: _RecordingStore | None) -> object:
    """Build an app state carrying *store* on the approval slice.

    Returns:
        The composed ``AppState``.
    """
    return make_app_state(
        slices={ApprovalStateSlice: {"store": store}} if store else None
    )


class TestRetireReviewApproval:
    async def test_the_plans_own_pending_approval_is_expired(self) -> None:
        store = _RecordingStore((_approval("parked"),))

        await retire_review_approval(_state(store), _plan())  # type: ignore[arg-type]  # composed AppState

        assert [item.status for item in store.saved] == [ApprovalStatus.EXPIRED]
        assert store.saved[0].id == as_uuid("parked")

    async def test_another_plans_approval_is_left_alone(self) -> None:
        """The metadata is the link; matching on source alone would take it."""
        store = _RecordingStore(
            (_approval("other", plan_id=str(as_uuid("other-plan"))),)
        )

        await retire_review_approval(_state(store), _plan())  # type: ignore[arg-type]  # composed AppState

        assert store.saved == []

    async def test_a_non_review_approval_is_left_alone(self) -> None:
        store = _RecordingStore((_approval("gate", source=ApprovalSource.REVIEW_GATE),))

        await retire_review_approval(_state(store), _plan())  # type: ignore[arg-type]  # composed AppState

        assert store.saved == []

    async def test_a_store_failure_stops_the_delete(self) -> None:
        """Retiring gates the delete, so a store that cannot answer blocks it.

        Swallowing here is what the ordering was inverted to remove: the plan
        would be deleted next while its approval stayed decidable, and the
        only remaining move would be to log a window that is already open.
        """
        store = mock_of[ApprovalStoreProtocol]()
        store.list_items.side_effect = RuntimeError("store down")

        with pytest.raises(RuntimeError, match="store down"):
            await retire_review_approval(_state(store), _plan())  # type: ignore[arg-type]  # composed AppState

    async def test_a_concurrent_decision_refuses_the_delete(self) -> None:
        """A verdict made while the plan existed outranks the deletion."""
        store = _RecordingStore((_approval("parked"),))
        store.cas_wins = False

        with pytest.raises(ConflictError, match="decided while the delete"):
            await retire_review_approval(_state(store), _plan())  # type: ignore[arg-type]  # composed AppState

    async def test_an_unwired_store_is_a_no_op(self) -> None:
        await retire_review_approval(_state(None), _plan())  # type: ignore[arg-type]  # composed AppState
