"""Unit tests for recording a human's project decision into the brain."""

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers._project_decision_record import record_project_decision
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.task_engine import TaskEngine
from synthorg.project_brain.models import BrainEntryKind, BrainEntryStatus
from synthorg.project_brain.service import ProjectBrainService
from synthorg.project_brain.state import ProjectBrainStateSlice
from tests._shared import as_uuid, make_app_state, mock_of, sid

pytestmark = pytest.mark.unit

_Configured = Any  # type: ignore[explicit-any]
_NOW = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)


def _task() -> Task:
    return Task(
        id=as_uuid("task-1"),
        title="Task 1",
        description="Do the thing",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="manager",
        assigned_to=str(as_uuid("agent-1")),
        status=TaskStatus.AWAITING_INPUT,
    )


def _decision_approval(
    *,
    decision: bool = True,
    task_id: str | None = "task-1",
    options: list[str] | None = None,
) -> ApprovalItem:
    metadata: dict[str, str] = {"options": json.dumps(options or [])}
    if decision:
        metadata["decision"] = "true"
    return ApprovalItem(
        id=as_uuid("appr-1"),
        action_type=NotBlankStr("decision:project"),
        title=NotBlankStr("Project decision requested"),
        description=NotBlankStr("Which web framework should we target?"),
        requested_by=NotBlankStr("agent-1"),
        risk_level=ApprovalRiskLevel.LOW,
        source=ApprovalSource.PARKED_CONTEXT,
        status=ApprovalStatus.PENDING,
        created_at=_NOW,
        task_id=NotBlankStr(str(as_uuid(task_id))) if task_id is not None else None,
        metadata=metadata,
    )


async def _seed(
    item: ApprovalItem,
    *,
    task: Task | None,
    with_brain: bool = True,
) -> tuple[_Configured, _Configured]:
    store = ApprovalStore()
    await store.add(item)
    engine = mock_of[TaskEngine](get_task=AsyncMock(return_value=task))
    brain = mock_of[ProjectBrainService](append_entry=AsyncMock(return_value=None))
    slices: dict[type[BaseFeatureStateSlice], dict[str, object]] | None = (
        {ProjectBrainStateSlice: {"service": brain}} if with_brain else None
    )
    state = make_app_state(approval_store=store, task_engine=engine, slices=slices)
    return state, brain


class TestRecordProjectDecision:
    async def test_records_decision_entry_on_approval(self) -> None:
        item = _decision_approval(options=["React", "Vue"])
        state, brain = await _seed(item, task=_task())

        await record_project_decision(
            state,
            sid("appr-1"),
            approved=True,
            decided_by="admin",
            decision_reason="React",
        )

        brain.append_entry.assert_awaited_once()
        kwargs = brain.append_entry.await_args.kwargs
        assert kwargs["project_id"] == "proj-1"
        assert kwargs["status"] is BrainEntryStatus.ACCEPTED
        assert kwargs["author"] == "admin"
        payload = kwargs["payload"]
        assert payload.entry_kind is BrainEntryKind.DECISION
        assert payload.decision_outcome == "React"
        assert payload.alternatives == ("React", "Vue")

    async def test_rejection_records_nothing(self) -> None:
        item = _decision_approval()
        state, brain = await _seed(item, task=_task())
        await record_project_decision(
            state,
            sid("appr-1"),
            approved=False,
            decided_by="admin",
            decision_reason="React",
        )
        brain.append_entry.assert_not_called()

    async def test_non_decision_approval_records_nothing(self) -> None:
        item = _decision_approval(decision=False)
        state, brain = await _seed(item, task=_task())
        await record_project_decision(
            state,
            sid("appr-1"),
            approved=True,
            decided_by="admin",
            decision_reason="React",
        )
        brain.append_entry.assert_not_called()

    async def test_blank_answer_records_nothing(self) -> None:
        item = _decision_approval()
        state, brain = await _seed(item, task=_task())
        await record_project_decision(
            state,
            sid("appr-1"),
            approved=True,
            decided_by="admin",
            decision_reason="   ",
        )
        brain.append_entry.assert_not_called()

    async def test_no_brain_service_is_noop(self) -> None:
        # Brain unwired (memory-gated): recording is skipped, no crash.
        item = _decision_approval()
        state, _ = await _seed(item, task=_task(), with_brain=False)
        await record_project_decision(
            state,
            sid("appr-1"),
            approved=True,
            decided_by="admin",
            decision_reason="React",
        )

    async def test_missing_task_records_nothing(self) -> None:
        item = _decision_approval()
        state, brain = await _seed(item, task=None)
        await record_project_decision(
            state,
            sid("appr-1"),
            approved=True,
            decided_by="admin",
            decision_reason="React",
        )
        brain.append_entry.assert_not_called()
