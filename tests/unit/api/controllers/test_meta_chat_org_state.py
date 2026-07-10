"""Unit tests for ``resolve_chat_org_state``, the /meta/chat org-state helper."""

from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs

from synthorg.api.controllers._meta_chat_org_state import resolve_chat_org_state
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.observability.events.meta import META_CHAT_DEPENDENCY_UNAVAILABLE
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.persistence.task_protocol import TaskRepository
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _task() -> Task:
    return Task(
        title="Fix login",
        description="Fix the login flow",
        type=TaskType.DEVELOPMENT,
        project="proj-platform",
        created_by="planner",
        assigned_to="agent-1",
        status=TaskStatus.IN_PROGRESS,
    )


def _connected_backend() -> PersistenceBackend:
    task_repo = mock_of[TaskRepository](
        query=AsyncMock(side_effect=[(_task(),), ()]),
        count=AsyncMock(side_effect=[1, 0]),
    )
    project_repo = mock_of[ProjectRepository](
        query=AsyncMock(return_value=()),
        count=AsyncMock(return_value=0),
    )
    backend: PersistenceBackend = mock_of[PersistenceBackend](
        is_connected=True,
        tasks=task_repo,
        projects=project_repo,
    )
    return backend


class TestResolveChatOrgState:
    async def test_returns_snapshot_when_connected(self) -> None:
        store = mock_of[ApprovalStoreProtocol](list_items=AsyncMock(return_value=()))
        state = make_app_state(approval_store=store)
        state.wire(PersistenceStateSlice, backend=_connected_backend())

        result = await resolve_chat_org_state(state)

        assert result is not None
        assert result.in_progress_total == 1
        assert result.has_work is True

    async def test_returns_none_when_persistence_absent(self) -> None:
        store = mock_of[ApprovalStoreProtocol](list_items=AsyncMock(return_value=()))
        state = make_app_state(approval_store=store)

        with capture_logs() as caplog:
            result = await resolve_chat_org_state(state)

        assert result is None
        events = [r.get("event") for r in caplog]
        assert META_CHAT_DEPENDENCY_UNAVAILABLE in events

    async def test_returns_none_when_approval_store_unwired(self) -> None:
        state = make_app_state()
        state.wire(PersistenceStateSlice, backend=_connected_backend())
        state.wire(ApprovalStateSlice, store=None)

        with capture_logs() as caplog:
            result = await resolve_chat_org_state(state)

        assert result is None
        events = [r.get("event") for r in caplog]
        assert META_CHAT_DEPENDENCY_UNAVAILABLE in events
