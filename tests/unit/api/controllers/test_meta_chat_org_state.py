"""Unit tests for ``resolve_chat_org_state``, the /meta/chat org-state helper."""

from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs

from synthorg.api.controllers._meta_chat_org_state import (
    _DEFAULT_MAX_ITEMS_PER_SECTION,
    _resolve_max_items,
    resolve_chat_org_state,
)
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.observability.events.meta import META_CHAT_DEPENDENCY_UNAVAILABLE
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.persistence.task_protocol import TaskRepository
from synthorg.settings.resolver import ConfigResolver
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

    async def test_returns_none_when_persistence_disconnected(self) -> None:
        # A wired-but-disconnected backend is treated like an absent one:
        # the reader must not run queries against a closed backend.
        store = mock_of[ApprovalStoreProtocol](list_items=AsyncMock(return_value=()))
        state = make_app_state(approval_store=store)
        backend: PersistenceBackend = mock_of[PersistenceBackend](is_connected=False)
        state.wire(PersistenceStateSlice, backend=backend)

        with capture_logs() as caplog:
            result = await resolve_chat_org_state(state)

        assert result is None
        events = [r.get("event") for r in caplog]
        assert META_CHAT_DEPENDENCY_UNAVAILABLE in events

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

    async def test_uses_live_setting_for_the_section_cap(self) -> None:
        # The resolved per-section cap is threaded into the reader, so a
        # capped section's query limit reflects the live setting.
        store = mock_of[ApprovalStoreProtocol](list_items=AsyncMock(return_value=()))
        resolver = mock_of[ConfigResolver](get_int=AsyncMock(return_value=3))
        state = make_app_state(approval_store=store, config_resolver=resolver)
        task_query = AsyncMock(side_effect=[(_task(),), ()])
        backend: PersistenceBackend = mock_of[PersistenceBackend](
            is_connected=True,
            tasks=mock_of[TaskRepository](
                query=task_query, count=AsyncMock(side_effect=[1, 0])
            ),
            projects=mock_of[ProjectRepository](
                query=AsyncMock(return_value=()), count=AsyncMock(return_value=0)
            ),
        )
        state.wire(PersistenceStateSlice, backend=backend)

        result = await resolve_chat_org_state(state)

        assert result is not None
        assert task_query.call_args_list[0].kwargs["limit"] == 3


class TestResolveMaxItems:
    async def test_falls_back_when_no_resolver_wired(self) -> None:
        state = make_app_state(config_resolver=None)
        assert await _resolve_max_items(state) == _DEFAULT_MAX_ITEMS_PER_SECTION

    async def test_returns_live_value(self) -> None:
        resolver = mock_of[ConfigResolver](get_int=AsyncMock(return_value=25))
        state = make_app_state(config_resolver=resolver)
        assert await _resolve_max_items(state) == 25

    async def test_falls_back_and_logs_when_resolver_raises(self) -> None:
        resolver = mock_of[ConfigResolver](
            get_int=AsyncMock(side_effect=RuntimeError("settings outage"))
        )
        state = make_app_state(config_resolver=resolver)

        with capture_logs() as caplog:
            result = await _resolve_max_items(state)

        assert result == _DEFAULT_MAX_ITEMS_PER_SECTION
        events = [r.get("event") for r in caplog]
        assert META_CHAT_DEPENDENCY_UNAVAILABLE in events
