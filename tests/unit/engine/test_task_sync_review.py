"""Tests for review-approval creation (risk derivation + failure tagging)."""

from unittest.mock import AsyncMock

import pytest

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.domain_errors import ConflictError
from synthorg.core.run_outcome import RunOutcome
from synthorg.core.task import Task
from synthorg.core.task_enums import Stakes, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.task_sync_review import create_review_approval
from tests._shared import as_uuid, mock_of, sid

pytestmark = pytest.mark.unit


def _task(*, stakes: Stakes, status: TaskStatus = TaskStatus.IN_REVIEW) -> Task:
    return Task(
        id=as_uuid("task-1"),
        title="Implement onboarding flow",
        description="desc",
        type=TaskType.DEVELOPMENT,
        project=NotBlankStr(sid("proj-1")),
        created_by=NotBlankStr("agent-x"),
        assigned_to=NotBlankStr("agent-x"),
        stakes=stakes,
        status=status,
    )


async def test_none_store_returns_none() -> None:
    result = await create_review_approval(
        None,
        agent_id="agent-x",
        task_id=sid("task-1"),
        task=_task(stakes=Stakes.HIGH),
        outcome=RunOutcome.SUCCEEDED,
    )
    assert result is None


async def test_succeeded_review_uses_completion_action_and_base_risk() -> None:
    store = mock_of[ApprovalStoreProtocol](add=AsyncMock())
    await create_review_approval(
        store,
        agent_id="agent-x",
        task_id=sid("task-1"),
        task=_task(stakes=Stakes.HIGH),
        outcome=RunOutcome.SUCCEEDED,
    )
    item = store.add.await_args.args[0]
    assert item.action_type == "review:task_completion"
    assert item.risk_level == ApprovalRiskLevel.HIGH
    assert item.title == "Review: Implement onboarding flow"
    # The title must never leak the raw task UUID.
    assert sid("task-1") not in item.title


async def test_empty_review_escalates_risk() -> None:
    store = mock_of[ApprovalStoreProtocol](add=AsyncMock())
    await create_review_approval(
        store,
        agent_id="agent-x",
        task_id=sid("task-1"),
        task=_task(stakes=Stakes.HIGH),
        outcome=RunOutcome.EMPTY,
    )
    item = store.add.await_args.args[0]
    assert item.action_type == "review:task_completion"
    assert item.risk_level == ApprovalRiskLevel.CRITICAL


async def test_failed_run_is_tagged_and_high_risk() -> None:
    store = mock_of[ApprovalStoreProtocol](add=AsyncMock())
    await create_review_approval(
        store,
        agent_id="agent-x",
        task_id=sid("task-1"),
        task=_task(stakes=Stakes.NORMAL, status=TaskStatus.FAILED),
        outcome=RunOutcome.FAILED,
    )
    item = store.add.await_args.args[0]
    assert item.action_type == "review:task_failed"
    # NORMAL stakes escalates one level on failure: MEDIUM -> HIGH, never LOW.
    assert item.risk_level == ApprovalRiskLevel.HIGH


async def test_store_failure_is_swallowed_not_raised() -> None:
    # Best-effort: a failing approval store must never lose the execution
    # result by propagating out of the sync path.
    store = mock_of[ApprovalStoreProtocol](
        add=AsyncMock(side_effect=RuntimeError("store down"))
    )
    result = await create_review_approval(
        store,
        agent_id="agent-x",
        task_id=sid("task-1"),
        task=_task(stakes=Stakes.HIGH),
        outcome=RunOutcome.SUCCEEDED,
    )
    assert result is None


async def test_transient_store_failure_retries_then_succeeds() -> None:
    # A transient store fault on the first attempt must not drop the approval:
    # the write is retried and the approval id is still returned.
    add = AsyncMock(side_effect=[RuntimeError("blip"), None])
    store = mock_of[ApprovalStoreProtocol](add=add)
    result = await create_review_approval(
        store,
        agent_id="agent-x",
        task_id=sid("task-1"),
        task=_task(stakes=Stakes.NORMAL, status=TaskStatus.FAILED),
        outcome=RunOutcome.FAILED,
    )
    assert result is not None
    assert add.await_count == 2


async def test_duplicate_id_conflict_is_treated_as_success() -> None:
    # The write is not idempotent under blind retry: if a prior attempt's write
    # landed but its ack was lost, the retry re-adds the same item and the store
    # raises ConflictError. That means the approval is already persisted, so it
    # must be reported as success (a returned id), never retried into a false
    # "dropped approval" alert.
    add = AsyncMock(side_effect=[RuntimeError("ack lost"), ConflictError("exists")])
    store = mock_of[ApprovalStoreProtocol](add=add)
    result = await create_review_approval(
        store,
        agent_id="agent-x",
        task_id=sid("task-1"),
        task=_task(stakes=Stakes.HIGH, status=TaskStatus.FAILED),
        outcome=RunOutcome.FAILED,
    )
    assert result is not None
    # The conflict short-circuits: no further retry attempts after it surfaces.
    assert add.await_count == 2


async def test_failed_outcome_store_failure_exhausts_retries() -> None:
    # A persistently failing store still never propagates, but the FAILED-path
    # write is retried the full budget before giving up (the failure would
    # otherwise be invisible in the queue).
    add = AsyncMock(side_effect=RuntimeError("store down"))
    store = mock_of[ApprovalStoreProtocol](add=add)
    result = await create_review_approval(
        store,
        agent_id="agent-x",
        task_id=sid("task-1"),
        task=_task(stakes=Stakes.HIGH, status=TaskStatus.FAILED),
        outcome=RunOutcome.FAILED,
    )
    assert result is None
    assert add.await_count == 3
