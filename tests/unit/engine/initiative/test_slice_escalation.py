"""Tests for the extend-workstream escalation."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.approval.initiative_slice import (
    INITIATIVE_SLICE_ACTION_TYPE,
    LEAF_ID_METADATA_KEY,
    PLAN_ID_METADATA_KEY,
)
from synthorg.core.approval import ApprovalItem
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.slice_escalation import SliceEscalationService
from tests._shared import FakeClock, as_uuid, sid

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


def _service(store: ApprovalStore) -> SliceEscalationService:
    return SliceEscalationService(approvals=store, clock=FakeClock())


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
                action_type=NotBlankStr(INITIATIVE_SLICE_ACTION_TYPE),
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
            action_type=NotBlankStr(INITIATIVE_SLICE_ACTION_TYPE),
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
            action_type=NotBlankStr(INITIATIVE_SLICE_ACTION_TYPE),
        )
        rejected = pending[0].model_copy(update={"status": ApprovalStatus.REJECTED})
        await store.save(rejected)

        assert await service.status_for(plan, leaf) is ApprovalStatus.REJECTED
