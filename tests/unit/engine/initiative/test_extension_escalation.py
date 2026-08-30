"""Tests for the extend-workstream escalation."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.approval.initiative_extension import (
    EXTENSION_ESCALATION_ACTOR,
    INITIATIVE_EXTENSION_ACTION_TYPE,
    LEAF_ID_METADATA_KEY,
    PLAN_ID_METADATA_KEY,
)
from synthorg.core.approval import ApprovalItem
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.extension_escalation import ExtensionEscalationService
from synthorg.engine.review_staffing.notices import DispatcherSource
from synthorg.notifications.dispatcher import NotificationDispatcher
from tests._shared import FakeClock, as_uuid, mock_of, sid

pytestmark = pytest.mark.unit

_PLAN_ID = "plan-1"
_PROJECT = "proj-1"
_WORKSTREAM = sid("ws-1")
_LEAF = sid("leaf-1")


def _item(item_id: str, *, parent_id: str | None = None) -> PlanItem:
    return PlanItem(
        id=item_id,
        parent_id=parent_id,
        title=NotBlankStr(f"Item {item_id[:4]}"),
        description=NotBlankStr("Do the thing"),
        acceptance_criteria=(NotBlankStr("it is done"),),
        expected_artifacts=(NotBlankStr("src/thing.py"),),
        unsplit_reason=NotBlankStr("depth backstop"),
    )


def _plan() -> Plan:
    now = datetime(2026, 7, 24, tzinfo=UTC)
    return Plan(
        id=as_uuid(_PLAN_ID),
        project=NotBlankStr(sid(_PROJECT)),
        project_name=NotBlankStr("Platform"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship it"),
        parent_task_id=NotBlankStr(sid("parent-1")),
        items=(_item(_WORKSTREAM), _item(_LEAF, parent_id=_WORKSTREAM)),
        objective_criteria=(NotBlankStr("the game is playable"),),
        created_at=now,
        updated_at=now,
    )


def _service(
    store: ApprovalStore, *, notifications: DispatcherSource = None
) -> ExtensionEscalationService:
    return ExtensionEscalationService(
        approvals=store, notifications=notifications, clock=FakeClock()
    )


class TestStatusFor:
    """The live status of a (plan, leaf)'s decision, or ``None``."""

    async def test_nothing_asked_yet_reads_as_none(self) -> None:
        plan = _plan()
        leaf = _item(_LEAF, parent_id=_WORKSTREAM)
        service = _service(ApprovalStore())

        assert await service.status_for(plan, leaf) is None

    async def test_an_open_ask_reads_pending(self) -> None:
        plan = _plan()
        workstream, leaf = plan.items
        service = _service(ApprovalStore())

        await service.escalate(plan, workstream, leaf)

        assert await service.status_for(plan, leaf) is ApprovalStatus.PENDING

    async def test_a_decision_for_another_leaf_does_not_match(self) -> None:
        plan = _plan()
        workstream, leaf = plan.items
        other_leaf = _item(sid("leaf-2"), parent_id=_WORKSTREAM)
        service = _service(ApprovalStore())

        await service.escalate(plan, workstream, leaf)

        assert await service.status_for(plan, other_leaf) is None

    async def test_a_foreign_item_under_the_same_action_type_does_not_match(
        self,
    ) -> None:
        """Provenance is checked, not assumed: the action type alone is not enough."""
        plan = _plan()
        _, leaf = plan.items
        store = ApprovalStore()
        await store.add(
            ApprovalItem(
                id=uuid4(),
                action_type=NotBlankStr(INITIATIVE_EXTENSION_ACTION_TYPE),
                title=NotBlankStr("Forged"),
                description=NotBlankStr("Not this organisation's own decision"),
                requested_by=NotBlankStr("someone-else"),
                risk_level=ApprovalRiskLevel.MEDIUM,
                source=ApprovalSource.REVIEW_GATE,
                status=ApprovalStatus.PENDING,
                created_at=datetime(2026, 7, 24, tzinfo=UTC),
                metadata={
                    PLAN_ID_METADATA_KEY: str(plan.id),
                    LEAF_ID_METADATA_KEY: leaf.id,
                },
            )
        )
        service = _service(store)

        assert await service.status_for(plan, leaf) is None

    async def test_a_pending_decision_wins_over_a_stale_expired_one(self) -> None:
        """An older EXPIRED record must not shadow a fresh re-ask.

        The store's own return order is not chronological, so seeding the
        stale record first (its natural creation order) reproduces the shape
        a real re-ask leaves behind: an EXPIRED decision the store still
        holds, followed by the PENDING one that replaced it. Picking
        whichever sorts first would read this leaf as still expired even
        though a decision is actually open.
        """
        plan = _plan()
        workstream, leaf = plan.items
        store = ApprovalStore()
        await store.add(
            ApprovalItem(
                id=uuid4(),
                action_type=NotBlankStr(INITIATIVE_EXTENSION_ACTION_TYPE),
                title=NotBlankStr("Extend workstream: stale ask"),
                description=NotBlankStr("A prior ask that lapsed unanswered"),
                requested_by=NotBlankStr(EXTENSION_ESCALATION_ACTOR),
                risk_level=ApprovalRiskLevel.MEDIUM,
                source=ApprovalSource.REVIEW_GATE,
                status=ApprovalStatus.EXPIRED,
                created_at=datetime(2026, 7, 1, tzinfo=UTC),
                metadata={
                    PLAN_ID_METADATA_KEY: str(plan.id),
                    LEAF_ID_METADATA_KEY: leaf.id,
                },
            )
        )
        service = _service(store)

        await service.escalate(plan, workstream, leaf)

        assert await service.status_for(plan, leaf) is ApprovalStatus.PENDING


class TestEscalate:
    """Raising the decision itself."""

    async def test_raises_a_pending_decision(self) -> None:
        plan = _plan()
        workstream, leaf = plan.items
        store = ApprovalStore()
        service = _service(store)

        await service.escalate(plan, workstream, leaf)

        pending = await store.list_items(
            status=ApprovalStatus.PENDING,
            action_type=NotBlankStr(INITIATIVE_EXTENSION_ACTION_TYPE),
        )
        assert len(pending) == 1
        assert pending[0].metadata[PLAN_ID_METADATA_KEY] == str(plan.id)
        assert pending[0].metadata[LEAF_ID_METADATA_KEY] == leaf.id

    async def test_a_rejected_decision_is_read_back_as_rejected(self) -> None:
        plan = _plan()
        workstream, leaf = plan.items
        store = ApprovalStore()
        service = _service(store)

        await service.escalate(plan, workstream, leaf)
        pending = await store.list_items(
            status=ApprovalStatus.PENDING,
            action_type=NotBlankStr(INITIATIVE_EXTENSION_ACTION_TYPE),
        )
        rejected = pending[0].model_copy(
            update={
                "status": ApprovalStatus.REJECTED,
                "decided_at": FakeClock().now(),
                "decided_by": NotBlankStr("operator"),
                "decision_reason": NotBlankStr("leaving it as delivered"),
            }
        )
        await store.save(rejected)

        assert await service.status_for(plan, leaf) is ApprovalStatus.REJECTED

    async def test_never_raises_a_second_decision_while_one_is_open(self) -> None:
        """The internal re-check: a caller's own pre-check is not trusted alone.

        Two concurrent recomputes finding the same leaf newly in need of an
        extension must not both raise a decision for it; only this method's
        own re-read of the store can catch that, since the caller's check and
        this write are not atomic.
        """
        plan = _plan()
        workstream, leaf = plan.items
        store = ApprovalStore()
        service = _service(store)

        await service.escalate(plan, workstream, leaf)
        await service.escalate(plan, workstream, leaf)

        pending = await store.list_items(
            status=ApprovalStatus.PENDING,
            action_type=NotBlankStr(INITIATIVE_EXTENSION_ACTION_TYPE),
        )
        assert len(pending) == 1


class TestNotify:
    """The notification sent alongside a raised decision."""

    async def test_no_dispatcher_source_sends_nothing(self) -> None:
        """The decision still lands with no source wired at all."""
        plan = _plan()
        workstream, leaf = plan.items
        store = ApprovalStore()
        service = _service(store)

        await service.escalate(plan, workstream, leaf)

        assert await service.status_for(plan, leaf) is ApprovalStatus.PENDING

    async def test_a_live_dispatcher_source_answering_none_sends_nothing(self) -> None:
        """A wired but currently-empty source is read the same as none at all."""
        plan = _plan()
        workstream, leaf = plan.items
        store = ApprovalStore()
        service = _service(store, notifications=lambda: None)

        await service.escalate(plan, workstream, leaf)

        assert await service.status_for(plan, leaf) is ApprovalStatus.PENDING

    async def test_a_successful_dispatch_carries_the_plan_and_leaf(self) -> None:
        plan = _plan()
        workstream, leaf = plan.items
        store = ApprovalStore()
        dispatch = AsyncMock(return_value=1)
        service = _service(
            store,
            notifications=lambda: mock_of[NotificationDispatcher](dispatch=dispatch),
        )

        await service.escalate(plan, workstream, leaf)

        dispatch.assert_awaited_once()
        assert dispatch.await_args is not None
        (notification,), _ = dispatch.await_args
        assert notification.metadata[PLAN_ID_METADATA_KEY] == str(plan.id)
        assert notification.metadata[LEAF_ID_METADATA_KEY] == leaf.id

    async def test_a_failed_dispatch_does_not_fail_the_escalation(self) -> None:
        """The decision it announces has already landed; a failed send is reported."""
        plan = _plan()
        workstream, leaf = plan.items
        store = ApprovalStore()
        dispatch = AsyncMock(side_effect=RuntimeError("notifications down"))
        service = _service(
            store,
            notifications=lambda: mock_of[NotificationDispatcher](dispatch=dispatch),
        )

        await service.escalate(plan, workstream, leaf)

        dispatch.assert_awaited_once()
        assert await service.status_for(plan, leaf) is ApprovalStatus.PENDING

    async def test_the_source_is_read_fresh_on_each_send(self) -> None:
        """Late-bound per call: a settings write rewiring notifications applies."""
        plan = _plan()
        workstream, leaf = plan.items
        other_leaf = _item(sid("leaf-2"), parent_id=_WORKSTREAM)
        store = ApprovalStore()
        first = AsyncMock(return_value=1)
        second = AsyncMock(return_value=1)
        live = [first]
        service = _service(
            store,
            notifications=lambda: mock_of[NotificationDispatcher](dispatch=live[0]),
        )

        await service.escalate(plan, workstream, leaf)
        live[0] = second
        await service.escalate(plan, workstream, other_leaf)

        first.assert_awaited_once()
        second.assert_awaited_once()
