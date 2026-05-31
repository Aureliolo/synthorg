"""Tests for the mission-control steering controller (project-scoped)."""

from typing import Any

import pytest

from synthorg.core.enums import TaskStatus
from synthorg.engine.cockpit.state import CockpitStateSlice
from synthorg.engine.intervention import NoOpSupersessionProposer, SteeringService
from tests._shared import LoopAsyncClient
from tests._shared.steering import FakeBrainService
from tests.unit.api.conftest import make_auth_headers
from tests.unit.api.fakes_backend import FakePersistenceBackend

_HEADERS = make_auth_headers("ceo")
_STEER_PATH = "/api/v1/cockpit/steering"


class _RecordingTaskEngine:
    """Records cancellations so the supersede endpoint can be asserted."""

    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def cancel_task(
        self, task_id: str, *, requested_by: str, reason: str
    ) -> tuple[None, None]:
        self.cancelled.append(task_id)
        return (None, None)

    async def get_task(self, task_id: str) -> None:
        """No tasks are tracked, so ownership validation always passes."""
        return

    async def list_tasks(
        self, *, status: TaskStatus, project: str, limit: int
    ) -> tuple[tuple[Any, ...], int]:
        return ((), 0)


@pytest.fixture(autouse=True)
async def steering_engine(
    async_test_client: LoopAsyncClient,
    fake_persistence: FakePersistenceBackend,
) -> _RecordingTaskEngine:
    """Wire a steering service (fake brain + recording task engine) per test.

    Returns:
        The recording task engine the supersede tests assert against.
    """
    app_state = async_test_client.app.state.app_state
    engine = _RecordingTaskEngine()
    app_state.wire(
        CockpitStateSlice,
        steering_service=SteeringService(
            brain_service=FakeBrainService(fake_persistence.project_brain),  # type: ignore[arg-type]
            brain_repo=fake_persistence.project_brain,
            task_engine=engine,  # type: ignore[arg-type]
            proposer=NoOpSupersessionProposer(),
        ),
    )
    return engine


@pytest.mark.unit
class TestSteeringController:
    async def test_issue_redirect_records_directive(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            _STEER_PATH,
            headers=_HEADERS,
            json={
                "project_id": "proj-1",
                "kind": "redirect",
                "text": "use Postgres not Mongo",
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["kind"] == "redirect"
        assert data["directive_id"]

    async def test_issue_rejects_pause_kind(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            _STEER_PATH,
            headers=_HEADERS,
            json={"project_id": "proj-1", "kind": "pause", "text": "halt"},
        )
        assert resp.status_code == 400

    async def test_issue_rejects_blank_text(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            _STEER_PATH,
            headers=_HEADERS,
            json={"project_id": "proj-1", "kind": "hint", "text": ""},
        )
        assert resp.status_code == 400

    async def test_list_active_returns_issued_directive(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await async_test_client.post(
            _STEER_PATH,
            headers=_HEADERS,
            json={"project_id": "proj-list", "kind": "hint", "text": "prefer the util"},
        )
        resp = await async_test_client.get(
            _STEER_PATH,
            headers=_HEADERS,
            params={"project_id": "proj-list"},
        )
        assert resp.status_code == 200
        directives = resp.json()["data"]
        assert len(directives) == 1
        assert directives[0]["text"] == "prefer the util"
        assert directives[0]["kind"] == "hint"

    async def test_supersede_cancels_confirmed_tasks(
        self,
        async_test_client: LoopAsyncClient,
        steering_engine: _RecordingTaskEngine,
    ) -> None:
        issued = await async_test_client.post(
            _STEER_PATH,
            headers=_HEADERS,
            json={"project_id": "proj-sup", "kind": "redirect", "text": "pivot"},
        )
        directive_id = issued.json()["data"]["directive_id"]
        resp = await async_test_client.post(
            f"{_STEER_PATH}/{directive_id}/supersede",
            headers=_HEADERS,
            json={"project_id": "proj-sup", "task_ids": ["t1", "t2"]},
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["directive_id"] == directive_id
        assert data["cancelled_task_ids"] == ["t1", "t2"]
        assert steering_engine.cancelled == ["t1", "t2"]
