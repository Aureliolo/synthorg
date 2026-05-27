"""Tests for the mission-control cockpit controller."""

from datetime import UTC, datetime
from typing import Any

import pytest
from litestar.testing import TestClient

from synthorg.core.enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.cockpit.state import CockpitStateSlice
from synthorg.persistence.flight_recorder_protocol import FlightRecorderFrame
from tests.unit.api.conftest import make_auth_headers
from tests.unit.api.fakes_backend import FakePersistenceBackend

_HEADERS = make_auth_headers("ceo")


@pytest.fixture(autouse=True)
def _ensure_cockpit_wired(test_client: TestClient[Any]) -> None:
    """Wire the cockpit services on the shared app if startup skipped them.

    The session-scoped app's once-only ``_install_runtime_services`` hook
    can leave the cockpit services unwired across re-entries (the known
    shared-app install leak); re-wiring here keeps the controller tests
    deterministic without depending on hook ordering.
    """
    app_state = test_client.app.state.app_state
    if app_state.slice(CockpitStateSlice).cockpit_service is None:
        from synthorg.api._app_wiring import _wire_cockpit_services

        _wire_cockpit_services(app_state)


def _seed_frame(
    backend: FakePersistenceBackend,
    *,
    execution_id: str,
    turn: int,
    cost: float = 0.5,
) -> None:
    frame = FlightRecorderFrame(
        id=NotBlankStr(f"{execution_id}-{turn}"),
        execution_id=NotBlankStr(execution_id),
        task_id=NotBlankStr("task-1"),
        agent_id=NotBlankStr("agent-1"),
        turn_index=turn,
        timestamp=datetime.now(UTC),
        response_summary=f"reply {turn}",
        decision="completed",
        cost=cost,
        status=TaskStatus.IN_PROGRESS,
    )
    # Direct dict seed: the controller reads through the same fake repo.
    backend.flight_recorder_frames._frames[frame.id] = frame


@pytest.mark.unit
class TestCockpitController:
    def test_snapshot_returns_live_activity(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get("/api/v1/cockpit/snapshot", headers=_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "agents" in body["data"]
        assert "active_count" in body["data"]

    def test_frames_returns_seeded_timeline(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        _seed_frame(fake_persistence, execution_id="exec-frames", turn=1)
        _seed_frame(fake_persistence, execution_id="exec-frames", turn=2)

        resp = test_client.get(
            "/api/v1/cockpit/flight-recorder/exec-frames/frames",
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        # ``getFlightRecorderFrames`` now uses opaque cursor pagination,
        # so the envelope is ``PaginatedResponse`` (``data`` is the page,
        # ``pagination`` carries cursor + has_more).
        turns = [f["turn_index"] for f in body["data"]]
        assert turns == [2, 1]
        assert body["pagination"]["has_more"] is False
        assert body["pagination"]["next_cursor"] is None

    def test_seek_reconstructs_prefix(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        for turn in (1, 2, 3):
            _seed_frame(fake_persistence, execution_id="exec-seek", turn=turn)

        resp = test_client.get(
            "/api/v1/cockpit/flight-recorder/exec-seek/seek/2",
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["turn_index"] == 2
        assert [f["turn_index"] for f in data["frames"]] == [1, 2]
        assert data["current_frame"]["turn_index"] == 2

    def test_hint_queues_steering(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.post(
            "/api/v1/cockpit/interventions/hint",
            headers=_HEADERS,
            json={
                "execution_id": "exec-1",
                "agent_id": "agent-1",
                "text": "use Postgres not Mongo",
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["applied"] is True
        assert data["kind"] == "hint"

    def test_redirect_queues_steering(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.post(
            "/api/v1/cockpit/interventions/redirect",
            headers=_HEADERS,
            json={
                "execution_id": "exec-1",
                "agent_id": "agent-1",
                "text": "pivot off the frontend",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["applied"] is True

    def test_hint_rejects_blank_text(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.post(
            "/api/v1/cockpit/interventions/hint",
            headers=_HEADERS,
            json={"execution_id": "exec-1", "agent_id": "agent-1", "text": ""},
        )
        assert resp.status_code == 400

    def test_pause_rejects_unknown_field(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.post(
            "/api/v1/cockpit/interventions/pause",
            headers=_HEADERS,
            json={"task_id": "t1", "reason": "stuck", "bogus": 1},
        )
        assert resp.status_code == 400
