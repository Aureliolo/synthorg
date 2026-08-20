"""An approval whose subject is gone must not stay actionable.

Delete-time retirement is edge-triggered and covers the deletes that know to.
A live run finished with every project, plan and task deleted and three
approvals still PENDING, still offering approve and reject, none carrying an
expiry, so lazy expiration could never reach them either. This is the
level-triggered complement: whatever left one stranded, the next pass closes it.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.run_recovery.orphan_approvals import retire_orphaned_approvals
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.task_protocol import TaskRepository
from tests._shared import as_uuid, mock_of, sid

pytestmark = pytest.mark.unit


def _persistence(live: Sequence[Task] = ()) -> Any:  # type: ignore[explicit-any]
    """A backend whose task store holds exactly *live*.

    Returns:
        The double, answering ``get`` for the ids it was given and ``None``
        for every other.
    """
    by_id = {str(task.id): task for task in live}
    return mock_of[PersistenceBackend](
        tasks=mock_of[TaskRepository](
            get=AsyncMock(side_effect=lambda task_id: by_id.get(str(task_id)))
        )
    )


def _approval(label: str, *, task_id: str | None) -> ApprovalItem:
    return ApprovalItem(
        id=as_uuid(label),
        action_type=NotBlankStr("code:write"),
        title=NotBlankStr("Approve to continue"),
        description="A question about something",
        requested_by=NotBlankStr("agent-1"),
        risk_level=ApprovalRiskLevel.MEDIUM,
        status=ApprovalStatus.PENDING,
        created_at=datetime(2026, 8, 20, tzinfo=UTC),
        task_id=NotBlankStr(task_id) if task_id is not None else None,
    )


def _task(label: str) -> Task:
    return Task(
        id=as_uuid(label),
        title="A real task",
        description="It exists",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=NotBlankStr("proj-1"),
        created_by=NotBlankStr("ceo"),
    )


async def _status(store: ApprovalStore, label: str) -> ApprovalStatus:
    item = await store.get(NotBlankStr(sid(label)))
    assert item is not None
    return item.status


class TestOrphanedApprovalsAreClosed:
    async def test_an_approval_whose_task_is_gone_is_expired(self) -> None:
        store = ApprovalStore()
        await store.add(_approval("appr-orphan", task_id=sid("task-gone")))

        retired = await retire_orphaned_approvals(
            store=store, persistence=_persistence()
        )

        assert retired == 1
        assert await _status(store, "appr-orphan") is ApprovalStatus.EXPIRED

    async def test_an_approval_whose_task_still_exists_is_left_alone(self) -> None:
        store = ApprovalStore()
        await store.add(_approval("appr-live", task_id=sid("task-live")))

        retired = await retire_orphaned_approvals(
            store=store, persistence=_persistence([_task("task-live")])
        )

        assert retired == 0
        assert await _status(store, "appr-live") is ApprovalStatus.PENDING

    async def test_an_approval_naming_no_task_is_left_alone(self) -> None:
        """It decides about something this pass cannot resolve either way."""
        store = ApprovalStore()
        await store.add(_approval("appr-subjectless", task_id=None))

        retired = await retire_orphaned_approvals(
            store=store, persistence=_persistence()
        )

        assert retired == 0
        assert await _status(store, "appr-subjectless") is ApprovalStatus.PENDING

    async def test_the_pass_is_idempotent(self) -> None:
        """It runs on a cadence; a second pass must find nothing left to do."""
        store = ApprovalStore()
        await store.add(_approval("appr-orphan", task_id=sid("task-gone")))
        await retire_orphaned_approvals(store=store, persistence=_persistence())

        again = await retire_orphaned_approvals(store=store, persistence=_persistence())

        assert again == 0

    async def test_a_mixed_queue_closes_only_the_orphans(self) -> None:
        store = ApprovalStore()
        await store.add(_approval("appr-live", task_id=sid("task-live")))
        await store.add(_approval("appr-orphan", task_id=sid("task-gone")))

        retired = await retire_orphaned_approvals(
            store=store, persistence=_persistence([_task("task-live")])
        )

        assert retired == 1
        assert await _status(store, "appr-live") is ApprovalStatus.PENDING
        assert await _status(store, "appr-orphan") is ApprovalStatus.EXPIRED

    async def test_a_decided_approval_is_not_reopened_or_recounted(self) -> None:
        """The operator works the same queue this pass sweeps; theirs wins."""
        store = ApprovalStore()
        item = _approval("appr-decided", task_id=sid("task-gone"))
        await store.add(item)
        await store.save(item.model_copy(update={"status": ApprovalStatus.APPROVED}))

        retired = await retire_orphaned_approvals(
            store=store, persistence=_persistence()
        )

        assert retired == 0
        assert await _status(store, "appr-decided") is ApprovalStatus.APPROVED
