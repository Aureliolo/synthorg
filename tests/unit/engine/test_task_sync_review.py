"""Tests for review-approval creation (risk derivation + failure tagging)."""

from unittest.mock import AsyncMock

import pytest

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.approval.protocol import ApprovalStoreProtocol
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
